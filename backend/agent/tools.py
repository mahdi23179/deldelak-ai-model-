"""Deldelak AI Model - agent tools.

Every tool runs inside the sandboxed workspace directory (config.WORKSPACE_DIR).
Paths are validated to stay inside the workspace (symlink + prefix safe).

HARD TRUTH (mission §6): this is NOT a real sandbox. The agent runs as the
Codespace container user which holds passwordless sudo. What we do here is
best-effort hardening, layered defense:
  1. env allowlist - child processes NEVER see GitHub tokens/secrets
  2. resource limits - RLIMIT_CPU / AS / NPROC / FSIZE per child
  3. process-group kill - timeouts kill the whole tree, no orphans
  4. cwd jail + sudo denial + dangerous-command approval gate
  5. append-only audit log (.state/audit.jsonl) for every execution
"""
from __future__ import annotations

import json
import os
import pathlib
import resource
import shutil
import subprocess
import threading
import time
import zipfile
from typing import Any, Callable

from .. import config, db

WS = config.WORKSPACE_DIR

__all__ = ["TOOLS", "is_sensitive", "dispatch"]


class ToolError(Exception):
    pass


_audit_lock = threading.Lock()


def _audit(event: str, detail: dict) -> None:
    """Append-only audit trail for tool executions (no secrets in payloads)."""
    try:
        config.ensure_dirs()
        line = json.dumps({"ts": time.time(), "event": event, **detail},
                          ensure_ascii=False, default=str)[:2000]
        with _audit_lock, open(config.AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------- path safety ----------------

def safe_path(rel: str, base: pathlib.Path | None = None) -> pathlib.Path:
    """Workspace jail - resists `/base-evil` prefix confusion AND symlinks:
    the fully-resolved path must be the base itself or a descendant."""
    base = (base or WS).resolve()
    p = (base / (rel or ".")).resolve()
    if p != base and base not in p.parents:
        raise ToolError("مسیر خارج از فضای کاری مجاز نیست")
    return p


# ---------------- sensitive detection ----------------

def is_sensitive(tool: str, args: dict) -> bool:
    if tool in ("files.delete",):
        return True
    if tool == "terminal.execute":
        cmd = str(args.get("command", ""))
        low = cmd.lower()
        if any(pat in low for pat in config.SENSITIVE_PATTERNS):
            return True
        # deleting files/dirs outside a project's own build artifacts
        if low.strip().startswith("rm ") and "-r" in low:
            return True
    return False

# ---------------- tool implementations ----------------

def _py_runner_available() -> str:
    return shutil.which("python3") or "python3"


def _limits_preexec() -> None:
    """Child-side resource limits (runs in the forked child before exec).
    Honest budgets for a 2-core/8GB Codespace."""
    resource.setrlimit(resource.RLIMIT_CPU, (config.AGENT_STEP_TIMEOUT, config.AGENT_STEP_TIMEOUT + 5))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 * 1024 * 1024, 2 * 1024 * 1024 * 1024))  # 2GB
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))  # 256MB
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def terminal_execute(args: dict, ctx: dict) -> dict:
    cmd = str(args.get("command", "")).strip()
    if not cmd:
        raise ToolError("command خالی است")
    if len(cmd) > 8000:
        raise ToolError("دستور بیش از حد بلند است")
    cwd = safe_path(args.get("cwd", ".")) if args.get("cwd") else ctx.get("cwd", WS)
    timeout = min(int(args.get("timeout", config.AGENT_STEP_TIMEOUT)), 300)
    low = cmd.lower()
    if any(pat in low for pat in config.SENSITIVE_PATTERNS) and not ctx.get("approved"):
        _audit("terminal.blocked_sensitive", {"task_id": ctx.get("task_id"), "cmd": cmd[:200]})
        raise ToolError("این دستور الگوی حساس دارد و بدون تأیید کاربر اجرا نمی‌شود (is_sensitive آن را علامت می‌زند)")
    t0 = time.time()
    _audit("terminal.execute", {"task_id": ctx.get("task_id"), "cmd": cmd[:300], "cwd": str(cwd)})
    proc = None
    try:
        proc = subprocess.Popen(
            ["bash", "-lc", cmd], cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=config.child_env(),
            start_new_session=True,  # own process group → whole-tree kill works
            preexec_fn=_limits_preexec,
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            out, rc = "", 124
        out = (out or "").strip()[:12000]
        res = {"ok": rc == 0, "output": out or "(بدون خروجی)", "exit_code": rc,
               "elapsed_s": round(time.time() - t0, 2)}
        if rc == 124:
            res["output"] = f"[timeout] دستور از مهلت {timeout} ثانیه عبور کرد و کل گروه فرایند کشته شد\n" + res["output"]
        _audit("terminal.result", {"task_id": ctx.get("task_id"), "exit": rc, "elapsed": res["elapsed_s"]})
        return res
    except Exception as e:
        if proc and proc.poll() is None:
            _kill_tree(proc)
        raise ToolError(f"اجرای دستور ناموفق بود: {type(e).__name__}")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the whole process group (bash children AND grandchildren)."""
    import signal
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def python_execute(args: dict, ctx: dict) -> dict:
    code = str(args.get("code", ""))
    if not code.strip():
        raise ToolError("code خالی است")
    tmp = safe_path(f".pytmp/exec_{int(time.time()*1000)}.py")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(code, encoding="utf-8")
    try:
        res = terminal_execute({"command": f'"{_py_runner_available()}" "{tmp}"'}, ctx)
        return res
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


def files_write(args: dict, ctx: dict) -> dict:
    path = safe_path(str(args.get("path", "")))
    if not str(args.get("path", "")).strip():
        raise ToolError("path الزامی است")
    content = str(args.get("content", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "output": f"نوشته شد: {path.relative_to(WS)} ({len(content)} کاراکتر)"}


def files_read(args: dict, ctx: dict) -> dict:
    path = safe_path(str(args.get("path", "")))
    if not path.is_file():
        raise ToolError(f"فایل پیدا نشد: {args.get('path')}")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"ok": True, "output": text[:12000]}


def files_edit(args: dict, ctx: dict) -> dict:
    path = safe_path(str(args.get("path", "")))
    if not path.is_file():
        raise ToolError(f"فایل پیدا نشد: {args.get('path')}")
    text = path.read_text(encoding="utf-8", errors="replace")
    find, rep = str(args.get("find", "")), str(args.get("replace", ""))
    if find not in text:
        raise ToolError("متن مورد جستجو در فایل یافت نشد")
    path.write_text(text.replace(find, rep, 1), encoding="utf-8")
    return {"ok": True, "output": f"ویرایش شد: {path.relative_to(WS)}"}


def files_delete(args: dict, ctx: dict) -> dict:
    path = safe_path(str(args.get("path", "")))
    if path == WS.resolve():
        raise ToolError("حذف کل فضای کاری مجاز نیست")
    if path.is_dir():
        shutil.rmtree(path)
        return {"ok": True, "output": f"پوشه حذف شد: {path.relative_to(WS)}"}
    if path.is_file():
        path.unlink()
        return {"ok": True, "output": f"فایل حذف شد: {path.relative_to(WS)}"}
    raise ToolError(f"مسیر پیدا نشد: {args.get('path')}")


def files_list(args: dict, ctx: dict) -> dict:
    path = safe_path(str(args.get("path", ".")))
    if not path.exists():
        raise ToolError(f"مسیر پیدا نشد: {args.get('path', '.')}")
    items = []
    for child in sorted(path.iterdir())[:200]:
        items.append({
            "name": child.name, "type": "dir" if child.is_dir() else "file",
            "size": child.stat().st_size if child.is_file() else None,
        })
    return {"ok": True, "output": json.dumps(items, ensure_ascii=False)}


def project_create(args: dict, ctx: dict) -> dict:
    name = str(args.get("name", "")).strip().replace(" ", "-")
    if not name or name.startswith("."):
        raise ToolError("نام پروژه نامعتبر است")
    template = str(args.get("template", "python"))
    pdir = safe_path(name)
    if pdir.exists():
        # idempotent: keep the projects table consistent even if the dir pre-exists
        if db.get_project_by_name(name) is None:
            db.create_project(name, str(pdir), template)
        return {"ok": True, "output": f"پروژه از قبل وجود دارد: {name}"}
    pdir.mkdir(parents=True)
    if template == "node":
        (pdir / "package.json").write_text(json.dumps({
            "name": name, "version": "1.0.0", "private": True,
            "scripts": {"start": "node index.js", "test": "node test.js"},
        }, indent=2), encoding="utf-8")
        (pdir / "index.js").write_text(
            "function add(a, b) { return a + b; }\nconsole.log('add(2,3) =', add(2, 3));\n"
            "module.exports = { add };\n", encoding="utf-8")
        (pdir / "test.js").write_text(
            "const { add } = require('./index');\n"
            "if (add(2, 3) !== 5) { console.error('FAIL'); process.exit(1); }\n"
            "console.log('PASS');\n", encoding="utf-8")
    else:
        (pdir / "main.py").write_text(
            'def add(a: float, b: float) -> float:\n    """جمع دو عدد"""\n    return a + b\n'
            'def div(a: float, b: float) -> float:\n    if b == 0:\n        raise ValueError("تقسیم بر صفر")\n'
            '    return a / b\n\nif __name__ == "__main__":\n    print("add(2,3) =", add(2, 3))\n',
            encoding="utf-8")
        (pdir / "test_main.py").write_text(
            "from main import add, div\nimport unittest\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
            "    def test_div(self):\n        self.assertEqual(div(6, 3), 2)\n"
            "    def test_div_zero(self):\n        with self.assertRaises(ValueError):\n            div(1, 0)\n\n"
            'if __name__ == "__main__":\n    unittest.main()\n', encoding="utf-8")
    (pdir / "README.md").write_text(f"# {name}\n\nساخته‌شده توسط عامل دلدلک.\n", encoding="utf-8")
    db.create_project(name, str(pdir), template)
    subprocess.run(["git", "init", "-q"], cwd=str(pdir), capture_output=True)
    return {"ok": True, "output": f"پروژه {name} ساخته شد (قالب {template}) در {pdir}"}


def _proj_dir(name: str) -> pathlib.Path:
    dbp = db.get_project_by_name(name)
    pdir = safe_path(name)
    if dbp and pathlib.Path(dbp["path"]).is_dir():
        return pathlib.Path(dbp["path"])
    if pdir.is_dir():
        return pdir
    raise ToolError(f"پروژه پیدا نشد: {name}")


def project_run(args: dict, ctx: dict) -> dict:
    pdir = _proj_dir(str(args.get("name", "")))
    if (pdir / "main.py").exists():
        return terminal_execute({"command": f'"{_py_runner_available()}" main.py', "cwd": str(pdir)}, ctx)
    if (pdir / "index.js").exists():
        return terminal_execute({"command": "node index.js", "cwd": str(pdir)}, ctx)
    raise ToolError("نقطه شروع پروژه (main.py یا index.js) پیدا نشد")


def project_test(args: dict, ctx: dict) -> dict:
    pdir = _proj_dir(str(args.get("name", "")))
    if any(pdir.glob("test_*.py")) or any(pdir.glob("*_test.py")):
        r = terminal_execute({"command": f'"{_py_runner_available()}" -m unittest discover -v 2>&1', "cwd": str(pdir)}, ctx)
    elif (pdir / "test.js").exists():
        r = terminal_execute({"command": "npm test --silent || node test.js", "cwd": str(pdir)}, ctx)
    else:
        raise ToolError("تستی برای پروژه پیدا نشد")
    out = str(r.get("output", ""))
    passed = out.count("ok") if "Ran" in out else (1 if r.get("ok") else 0)
    failed = out.count("FAIL") + out.count("ERROR")
    db.save_test_result(ctx.get("task_id"), pdir.name, passed, failed, out[-2000:])
    return r


def project_export(args: dict, ctx: dict) -> dict:
    pdir = _proj_dir(str(args.get("name", "")))
    config.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    zpath = config.EXPORTS_DIR / f"{pdir.name}.zip"
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(pdir.rglob("*")):
            if ".git" in f.parts or f.is_dir():
                continue
            zf.write(f, f.relative_to(pdir))
    return {"ok": True, "output": f"خروجی ZIP ساخته شد: {zpath} ({zpath.stat().st_size} بایت)",
            "zip_path": str(zpath), "zip_name": zpath.name}


def git_cmd(sub: str, args: dict, ctx: dict) -> dict:
    pdir = _proj_dir(str(args.get("name", "")))
    if sub == "status":
        return terminal_execute({"command": "git status --short --branch", "cwd": str(pdir)}, ctx)
    if sub == "diff":
        return terminal_execute({"command": "git diff HEAD", "cwd": str(pdir)}, ctx)
    if sub == "commit":
        msg = str(args.get("message", "تغییرات عامل دلدلک"))[:200]
        terminal_execute({"command": "git add -A", "cwd": str(pdir)}, ctx)
        # argv list - the message can never become shell code (audit A7)
        t0 = time.time()
        try:
            r = subprocess.run(
                ["git", "-c", "user.name=deldelak-agent", "-c", "user.email=agent@deldelak.local",
                 "commit", "-m", msg],
                cwd=str(pdir), capture_output=True, text=True, timeout=60,
                env=config.child_env(),
            )
            out = ((r.stdout or "") + (r.stderr or "")).strip()[:4000]
            return {"ok": r.returncode == 0, "output": out or "(کامیت ثبت شد)",
                    "exit_code": r.returncode, "elapsed_s": round(time.time() - t0, 2)}
        except Exception as e:
            return {"ok": False, "output": f"{type(e).__name__}: {e}", "exit_code": -1}
    raise ToolError(f"ابزار git ناشناخته: {sub}")


def respond(args: dict, ctx: dict) -> dict:
    text = str(args.get("text", "")).strip()
    return {"ok": True, "output": text or "(پاسخ خالی)"}


TOOLS: dict[str, Callable[[dict, dict], dict]] = {
    "terminal.execute": terminal_execute,
    "python.execute": python_execute,
    "files.write": files_write,
    "files.read": files_read,
    "files.edit": files_edit,
    "files.delete": files_delete,
    "files.list": files_list,
    "project.create": project_create,
    "project.run": project_run,
    "project.test": project_test,
    "project.export": project_export,
    "git.status": lambda a, c: git_cmd("status", a, c),
    "git.diff": lambda a, c: git_cmd("diff", a, c),
    "git.commit": lambda a, c: git_cmd("commit", a, c),
    "respond": respond,
}


def dispatch(tool: str, args: dict, ctx: dict) -> dict:
    fn = TOOLS.get(tool)
    if fn is None:
        raise ToolError(f"ابزار ناشناخته: {tool}")
    return fn(args, ctx)
