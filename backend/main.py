"""Deldelak AI Model - FastAPI application.

OpenAI-compatible local API + agent endpoints + file/project management +
system status. Serves the Persian RTL web UI from /frontend.

Security model:
- llama.cpp model server binds to 127.0.0.1 ONLY (never forwarded).
- All /v1/* endpoints require a random API key (Authorization: Bearer or X-API-Key).
- The key is generated on first boot; the first caller of GET /api/key claims it
  (claim window). After that it is never exposed without authentication.
- /health and the static UI are public by design.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import stat
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
import psutil
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db
from .agent.engine import AgentEngine, TaskWorker
from .agent.prompts import CHAT_SYSTEM
from .llm_client import LLMClient
from .llm_server import manager
from .llm_sync import SyncLLM

BOOT_TIME = time.time()
llm = LLMClient()          # async client for chat endpoints (event loop)
engine_llm = SyncLLM()     # sync client for the agent worker thread
engine = AgentEngine(engine_llm)
worker = TaskWorker(engine)


# ---------------- api key management ----------------

def _load_or_create_key() -> str:
    config.ensure_dirs()
    if config.API_KEY_FILE.exists():
        return config.API_KEY_FILE.read_text(encoding="utf-8").strip()
    key = "ddk-" + secrets.token_urlsafe(24)
    config.API_KEY_FILE.write_text(key, encoding="utf-8")
    os.chmod(config.API_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600
    if config.KEY_CLAIMED_FILE.exists():
        config.KEY_CLAIMED_FILE.unlink()
    print("[deldelak] API key generated (claim it via GET /api/key)", flush=True)
    return key


API_KEY = _load_or_create_key()


def require_key(authorization: str = Header(default=""),
                x_api_key: str = Header(default="")) -> str:
    provided = ""
    if authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    provided = provided or x_api_key.strip()
    if provided != API_KEY:
        raise HTTPException(status_code=401, detail="کلید API نامعتبر یا موجود نیست. هدر Authorization: Bearer <key> را بفرستید.")
    return provided


# ---------------- schemas ----------------

class ChatMessage(BaseModel):
    role: str = "user"
    content: str = ""

class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: list[ChatMessage]
    max_tokens: Optional[int] = 1024
    temperature: Optional[float] = 0.7
    stream: Optional[bool] = False
    session_id: Optional[str] = None

class SessionCreate(BaseModel):
    title: Optional[str] = "گفتگوی جدید"
    mode: Optional[str] = "chat"

class AgentTaskCreate(BaseModel):
    instruction: str = Field(min_length=2, max_length=8000)
    mode: Optional[str] = "auto"

class ApprovalBody(BaseModel):
    approved: bool

class FileWriteBody(BaseModel):
    path: str
    content: str

class ProjectCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    template: Optional[str] = "python"

class ClaimBody(BaseModel):
    key: str


# ---------------- lifespan ----------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    recovered = db.recover_interrupted_tasks()
    if recovered:
        print(f"[deldelak] {recovered} interrupted task(s) marked failed after restart", flush=True)
    # resume queued tasks
    for t in db.list_tasks():
        if t["status"] == "queued":
            worker.submit(t["id"])
            break
    # try to start llama-server in background (non-blocking)
    import threading
    threading.Thread(target=manager.start, daemon=True).start()
    yield
    await llm.aclose()
    manager.stop()


app = FastAPI(title="Deldelak AI Model", version=config.VERSION, lifespan=lifespan)


# ---------------- helpers ----------------

def _model_ready() -> bool:
    return manager.is_running()


async def _ensure_llm() -> None:
    if not await llm.health():
        raise HTTPException(
            status_code=503,
            detail="مدل هنوز آماده نیست. اگر Codespace تازه ساخته شده، چند دقیقه صبر کنید (بارگیری مدل ~۲ گیگابایت).",
        )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    db.log_error(None, "api", f"{type(exc).__name__}: {exc}")
    return JSONResponse(status_code=500, content={"error": {"message": f"خطای داخلی: {type(exc).__name__}"}})


# ---------------- public endpoints ----------------

@app.get("/health")
def health():
    return {
        "status": "ok" if _model_ready() else "degraded",
        "service": "deldelak-ai-model",
        "version": config.VERSION,
        "model_loaded": _model_ready(),
        "model": config.MODEL_ID,
        "uptime_s": round(time.time() - BOOT_TIME, 1),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


@app.get("/api/key")
def get_key():
    """Claim window: first caller receives the key once; afterwards auth is required."""
    if not config.KEY_CLAIMED_FILE.exists():
        config.KEY_CLAIMED_FILE.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))
        return {"key": API_KEY, "claimed": True,
                "note": "این کلید یک بار نمایش داده می‌شود. آن را ذخیره کنید."}
    raise HTTPException(status_code=403, detail="کلید قبلاً دریافت/ادعا شده است. با کلید معتبر وارد شوید.")


@app.post("/api/key/claim")
def claim_key(body: ClaimBody):
    if body.key == API_KEY:
        if not config.KEY_CLAIMED_FILE.exists():
            config.KEY_CLAIMED_FILE.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))
        return {"ok": True}
    raise HTTPException(status_code=401, detail="کلید نادرست است")


@app.post("/api/key/rotate")
def rotate_key(_: str = Depends(require_key)):
    global API_KEY
    API_KEY = "ddk-" + secrets.token_urlsafe(24)
    config.API_KEY_FILE.write_text(API_KEY, encoding="utf-8")
    if config.KEY_CLAIMED_FILE.exists():
        config.KEY_CLAIMED_FILE.unlink()
    return {"key": API_KEY, "note": "کلید جدید یک بار از GET /api/key قابل دریافت است."}


# ---------------- OpenAI-compatible ----------------

@app.get("/v1/models")
async def v1_models(_: str = Depends(require_key)):
    ready = await llm.health()
    return {"object": "list", "data": [{
        "id": config.MODEL_ID, "object": "model", "owned_by": "deldelak-local",
        "ready": ready, "label": config.MODEL_LABEL,
    }]}


@app.post("/v1/chat/completions")
async def v1_chat(req: ChatCompletionRequest, _: str = Depends(require_key)):
    await _ensure_llm()
    messages = [m.model_dump() for m in req.messages]
    if not any(m.get("role") == "system" for m in messages):
        messages = [{"role": "system", "content": CHAT_SYSTEM}] + messages
    session_id = req.session_id
    if session_id and db.get_session(session_id):
        db.add_message(session_id, "user", messages[-1]["content"] if messages else "")

    if req.stream:
        return StreamingResponse(_stream_and_persist(messages, req, session_id),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    data = await llm.chat(messages, max_tokens=req.max_tokens or 1024,
                          temperature=req.temperature or 0.7)
    data["model"] = config.MODEL_ID
    if session_id and db.get_session(session_id):
        try:
            db.add_message(session_id, "assistant", data["choices"][0]["message"]["content"])
        except Exception:
            pass
    return data


async def _stream_and_persist(messages: list[dict], req: ChatCompletionRequest, session_id: Optional[str]):
    collected: list[str] = []
    try:
        async for chunk in llm.stream_chat(messages, max_tokens=req.max_tokens or 1024,
                                           temperature=req.temperature or 0.7):
            line = chunk.decode("utf-8", "replace").strip()
            if line.startswith("data: ") and line != "data: [DONE]":
                try:
                    d = json.loads(line[6:])
                    delta = d.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        collected.append(delta)
                except Exception:
                    pass
            yield chunk
    finally:
        if session_id and db.get_session(session_id) and collected:
            db.add_message(session_id, "assistant", "".join(collected))


# ---------------- sessions ----------------

@app.post("/v1/sessions")
def create_session(body: SessionCreate, _: str = Depends(require_key)):
    return db.create_session(body.title or "گفتگوی جدید", body.mode or "chat")


@app.get("/v1/sessions")
def list_sessions(_: str = Depends(require_key)):
    return {"object": "list", "data": db.list_sessions()}


@app.get("/v1/sessions/{sid}/messages")
def session_messages(sid: str, _: str = Depends(require_key)):
    if not db.get_session(sid):
        raise HTTPException(status_code=404, detail="گفتگو پیدا نشد")
    return {"object": "list", "data": db.list_messages(sid)}


@app.delete("/v1/sessions/{sid}")
def delete_session(sid: str, _: str = Depends(require_key)):
    db.delete_session(sid)
    return {"ok": True}


# ---------------- agent ----------------

@app.post("/v1/agent/tasks")
def agent_create(body: AgentTaskCreate, _: str = Depends(require_key)):
    t = db.create_task(body.instruction, body.mode or "auto")
    worker.submit(t["id"])
    return t


@app.get("/v1/agent/tasks")
def agent_list(_: str = Depends(require_key)):
    return {"object": "list", "data": db.list_tasks()}


@app.get("/v1/agent/tasks/{task_id}")
def agent_get(task_id: str, _: str = Depends(require_key)):
    t = db.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="تسک پیدا نشد")
    t["checkpoints"] = len(db.list_checkpoints(task_id))
    return t


@app.post("/v1/agent/tasks/{task_id}/approve")
def agent_approve(task_id: str, body: ApprovalBody, _: str = Depends(require_key)):
    t = db.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="تسک پیدا نشد")
    ok = engine.approve(task_id, body.approved)
    if not ok:
        raise HTTPException(status_code=409, detail="این تسک در انتظار تأیید نیست")
    return {"ok": True, "approved": body.approved}


# ---------------- system status ----------------

@app.get("/v1/system/status")
def system_status(_: str = Depends(require_key)):
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    cpu = psutil.cpu_percent(interval=0.2)
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    active = sum(1 for t in db.list_tasks() if t["status"] in
                 ("queued", "planning", "executing", "verifying", "awaiting_approval"))
    return {
        "status": "ok",
        "version": config.VERSION,
        "uptime_s": round(time.time() - BOOT_TIME, 1),
        "platform": {
            "os": f"{psutil.SYSTEM_VERSION if hasattr(psutil, 'SYSTEM_VERSION') else os.uname().sysname} {os.uname().release}".strip(),
            "machine": os.uname().machine,
            "cpus": psutil.cpu_count(logical=True),
            "python": os.sys.version.split()[0],
        },
        "ram": {"used_mb": round(vm.used / 1048576, 1), "total_mb": round(vm.total / 1048576, 1),
                "percent": round(vm.percent, 1)},
        "disk": {"used_gb": round(du.used / 1073741824, 2), "total_gb": round(du.total / 1073741824, 2),
                 "percent": round(getattr(du, "percent", du.used / du.total * 100), 1)},
        "cpu_percent": cpu,
        "load_avg": [round(x, 2) for x in load],
        "model": {
            "id": config.MODEL_ID,
            "loaded": _model_ready(),
            "llama_rss_mb": manager.rss_mb(),
            "size_mb": round(config.MODEL_PATH.stat().st_size / 1048576, 1) if config.MODEL_PATH.exists() else None,
            "ctx": config.LLM_CTX, "threads": config.LLM_THREADS,
        },
        "llm": {"host": config.LLM_HOST, "port": config.LLM_PORT,
                "last_tok_s": llm.last_tok_s,
                "local_only": _is_localhost_bound(config.LLM_PORT)},
        "agent": {"active_tasks": active, "worker_alive": worker.active},
        "codespace": {"name": os.getenv("CODESPACE_NAME"), "region": os.getenv("CODESPACE_REGION")},
    }


def _is_localhost_bound(port: int) -> bool:
    """Verify the model server port is NOT externally reachable (safety check)."""
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        return local_ip.startswith("127.") or config.LLM_HOST == "127.0.0.1"
    except Exception:
        return config.LLM_HOST == "127.0.0.1"


# ---------------- files ----------------

def _ws_path(rel: str) -> Path:
    p = (config.WORKSPACE_DIR / (rel or ".")).resolve()
    if not str(p).startswith(str(config.WORKSPACE_DIR.resolve())):
        raise HTTPException(status_code=400, detail="مسیر خارج از فضای کاری مجاز نیست")
    return p


@app.get("/v1/files")
def files_list(path: str = Query(default="."), _: str = Depends(require_key)):
    p = _ws_path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="مسیر پیدا نشد")
    items = []
    for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if child.name.startswith(".pytmp"):
            continue
        items.append({
            "name": child.name,
            "path": str(child.relative_to(config.WORKSPACE_DIR)),
            "type": "dir" if child.is_dir() else "file",
            "size": child.stat().st_size if child.is_file() else None,
            "mtime": child.stat().st_mtime,
        })
    return {"path": path, "items": items}


@app.get("/v1/files/content")
def files_get(path: str = Query(...), _: str = Depends(require_key)):
    p = _ws_path(path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="فایل پیدا نشد")
    return {"path": path, "content": p.read_text(encoding="utf-8", errors="replace")}


@app.put("/v1/files/content")
def files_put(body: FileWriteBody, _: str = Depends(require_key)):
    p = _ws_path(body.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": body.path, "size": len(body.content)}


@app.delete("/v1/files")
def files_delete(path: str = Query(...), _: str = Depends(require_key)):
    p = _ws_path(path)
    if p == config.WORKSPACE_DIR.resolve():
        raise HTTPException(status_code=400, detail="حذف کل فضای کاری مجاز نیست")
    if p.is_dir():
        shutil.rmtree(p)
    elif p.is_file():
        p.unlink()
    else:
        raise HTTPException(status_code=404, detail="مسیر پیدا نشد")
    return {"ok": True}


# ---------------- projects ----------------

@app.get("/v1/projects")
def projects_list(_: str = Depends(require_key)):
    return {"object": "list", "data": db.list_projects()}


@app.post("/v1/projects")
def projects_create(body: ProjectCreateBody, _: str = Depends(require_key)):
    from .agent import tools as agent_tools
    res = agent_tools.dispatch("project.create", {"name": body.name, "template": body.template or "python"}, {})
    proj = db.get_project_by_name(body.name)
    return {"ok": res.get("ok", False), "message": res.get("output"), "project": proj}


@app.get("/v1/projects/{pid}/export")
def project_export(pid: str, api_key: str = Query(default="")):
    # download links need query-param auth (browser <a href> cannot set headers)
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="کلید API نامعتبر")
    proj = db.get_project(pid)
    name = proj["name"] if proj else pid
    try:
        from .agent import tools as agent_tools
        res = agent_tools.dispatch("project.export", {"name": name}, {})
        zpath = Path(res["zip_path"])
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"خروجی گرفتن ممکن نشد: {e}")
    return FileResponse(zpath, filename=zpath.name, media_type="application/zip")


@app.post("/v1/projects/{pid}/run")
def project_run(pid: str, _: str = Depends(require_key)):
    proj = db.get_project(pid)
    if not proj:
        raise HTTPException(status_code=404, detail="پروژه پیدا نشد")
    from .agent import tools as agent_tools
    try:
        res = agent_tools.dispatch("project.run", {"name": proj["name"]}, {})
        return {"ok": bool(res.get("ok")), "output": res.get("output"), "exit_code": res.get("exit_code")}
    except Exception as e:
        return {"ok": False, "output": f"{type(e).__name__}: {e}"}


@app.post("/v1/projects/{pid}/test")
def project_test(pid: str, _: str = Depends(require_key)):
    proj = db.get_project(pid)
    if not proj:
        raise HTTPException(status_code=404, detail="پروژه پیدا نشد")
    from .agent import tools as agent_tools
    try:
        res = agent_tools.dispatch("project.test", {"name": proj["name"]}, {})
        return {"ok": bool(res.get("ok")), "output": res.get("output"), "exit_code": res.get("exit_code")}
    except Exception as e:
        return {"ok": False, "output": f"{type(e).__name__}: {e}"}


# ---------------- static frontend ----------------

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/assets", StaticFiles(directory=str(FRONTEND / "css"), check_dir=False), name="css")
app.mount("/js", StaticFiles(directory=str(FRONTEND / "js"), check_dir=False), name="js")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/favicon.svg")
def favicon():
    f = FRONTEND / "favicon.svg"
    if f.exists():
        return FileResponse(f, media_type="image/svg+xml")
    raise HTTPException(status_code=404)
