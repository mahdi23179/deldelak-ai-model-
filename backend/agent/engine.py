"""Deldelak AI Model - Agent Engine: Planner → Executor → Verifier.

- Planner: LLM produces a JSON step plan (tools only).
- Executor: runs tools in the sandboxed workspace, checkpoints after each step,
  pauses for explicit user approval on sensitive operations.
- Verifier: LLM judges whether the goal was actually met; on failure a repair
  cycle re-plans (bounded by AGENT_MAX_CYCLES and a wall-clock budget).

All state is persisted in SQLite so tasks survive restarts.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from .. import config, db
from ..llm_client import parse_json_object
from . import prompts, tools

_approval_events: dict[str, threading.Event] = {}
_approval_results: dict[str, bool] = {}
_lock = threading.Lock()


class AgentEngine:
    def __init__(self, llm: Any) -> None:
        # llm must expose sync chat()/chat_json() (backend.llm_sync.SyncLLM in
        # production; a sync mock in tests). The engine runs in a worker thread,
        # so blocking calls are correct here.
        self.llm = llm

    # ---------- approval ----------
    def approve(self, task_id: str, approved: bool) -> bool:
        with _lock:
            ev = _approval_events.get(task_id)
            if ev is None:
                return False
            _approval_results[task_id] = bool(approved)
            ev.set()
        return True

    # ---------- main loop ----------
    def run_task(self, task_id: str) -> None:
        task = db.get_task(task_id)
        if not task:
            return
        instruction = task["instruction"]
        deadline = time.time() + config.AGENT_TASK_BUDGET
        prior: list[dict] = []
        verdict: dict = {}
        try:
            for cycle in range(1, config.AGENT_MAX_CYCLES + 1):
                db.update_task(task_id, cycles=cycle, status="planning")
                plan = self._plan(instruction, prior, is_replan=(cycle > 1))
                if plan.get("respond_only"):
                    db.update_task(task_id, status="completed",
                                   result=json.dumps({"summary": plan["respond_text"]}, ensure_ascii=False))
                    return
                if time.time() > deadline:
                    raise TimeoutError("زمان اجرای تسک به پایان رسید")

                db.update_task(task_id, status="executing")
                exec_ok = True
                for i, step in enumerate(plan["steps"][: config.AGENT_MAX_STEPS]):
                    if time.time() > deadline:
                        raise TimeoutError("زمان اجرای تسک به پایان رسید")
                    sid = db.add_step(task_id, "execute", i + 1, step["tool"], step.get("title"),
                                      step.get("args"), status="running")
                    if tools.is_sensitive(step["tool"], step.get("args", {})):
                        db.finish_step(sid, "awaiting_approval", "این عملیات حساس است و نیازمند تأیید شماست.")
                        db.update_task(task_id, status="awaiting_approval")
                        if not self._wait_approval(task_id):
                            db.update_task(task_id, status="executing")
                            prior.append({"tool": step["tool"], "title": step.get("title"),
                                          "output": "کاربر رد کرد", "ok": False})
                            continue
                        db.finish_step(sid, "running", "تأیید شد؛ در حال اجرا...")
                        db.update_task(task_id, status="executing")
                        step_ctx = {"task_id": task_id, "cwd": plan.get("cwd"), "approved": True}
                    else:
                        step_ctx = {"task_id": task_id, "cwd": plan.get("cwd")}
                    try:
                        res = tools.dispatch(step["tool"], step.get("args", {}), step_ctx)
                        db.finish_step(sid, "ok" if res.get("ok") else "error",
                                       str(res.get("output", ""))[:8000])
                        prior.append({"tool": step["tool"], "title": step.get("title"),
                                      "output": str(res.get("output", ""))[:1500], "ok": bool(res.get("ok"))})
                        if not res.get("ok"):
                            exec_ok = False
                    except Exception as e:
                        db.finish_step(sid, "error", f"{type(e).__name__}: {e}")
                        db.log_error(task_id, "executor", f"{step['tool']}: {e}")
                        prior.append({"tool": step["tool"], "title": step.get("title"),
                                      "output": f"{type(e).__name__}: {e}", "ok": False})
                        exec_ok = False
                    db.save_checkpoint(task_id, {"cycle": cycle, "step": i + 1,
                                                 "status": "step_done", "ok": exec_ok})

                db.update_task(task_id, status="verifying")
                verdict = self._verify(instruction, prior)
                if verdict.get("pass"):
                    db.update_task(task_id, status="completed",
                                   result=json.dumps({"summary": verdict.get("summary", ""),
                                                      "verifier": verdict}, ensure_ascii=False))
                    return
                db.log_error(task_id, "verifier", json.dumps(verdict, ensure_ascii=False)[:2000])
                prior.append({"tool": "verifier", "title": "بازرسی",
                              "output": verdict.get("summary", ""), "ok": False})
            db.update_task(task_id, status="completed_with_issues",
                           result=json.dumps({"summary": verdict.get("summary", "پس از ۳ تلاش اصلاحی، هدف کامل محقق نشد."),
                                              "verifier": verdict}, ensure_ascii=False))
        except Exception as e:
            db.log_error(task_id, "engine", f"{type(e).__name__}: {e}")
            db.update_task(task_id, status="failed", error=f"{type(e).__name__}: {e}")

    def _wait_approval(self, task_id: str) -> bool:
        ev = threading.Event()
        with _lock:
            _approval_events[task_id] = ev
            _approval_results.pop(task_id, None)
        approved = ev.wait(timeout=config.AGENT_APPROVAL_TIMEOUT) and _approval_results.get(task_id, False)
        with _lock:
            _approval_events.pop(task_id, None)
            _approval_results.pop(task_id, None)
        if approved:
            db.save_checkpoint(task_id, {"status": "approved"})
        else:
            db.save_checkpoint(task_id, {"status": "denied_or_timeout"})
        return approved

    # ---------- LLM phases ----------
    def _plan(self, instruction: str, prior: list[dict], is_replan: bool) -> dict:
        msgs = [{"role": "system", "content": prompts.PLANNER_SYSTEM},
                {"role": "user", "content": f"درخواست کاربر:\n{instruction}"}]
        if prior:
            ctx = json.dumps(prior[-10:], ensure_ascii=False, indent=1)
            if is_replan:
                msgs[1]["content"] += "\n\n" + prompts.REPLAN_HINT + "\nنتایج قبلی:\n" + ctx
            else:
                msgs[1]["content"] += "\n\nنتایج گام‌های قبلی (در صورت وجود):\n" + ctx
        out = self.llm.chat_json(msgs, max_tokens=config.AGENT_LLM_MAX_TOKENS)
        if "steps" not in out and "text" in out:
            return {"respond_only": True, "respond_text": str(out["text"])}
        steps = out.get("steps")
        if not isinstance(steps, list) or not steps:
            # conversational fallback: ask model for a direct answer
            ans = self.llm.chat(
                [{"role": "system", "content": prompts.CHAT_SYSTEM},
                 {"role": "user", "content": instruction}], max_tokens=700)
            return {"respond_only": True, "respond_text": _text(ans)}
        clean = []
        for s in steps:
            if not isinstance(s, dict) or "tool" not in s:
                continue
            clean.append({"title": str(s.get("title", s["tool"])), "tool": str(s["tool"]),
                          "args": s.get("args") or {}})
        if not clean:
            ans = self.llm.chat(
                [{"role": "system", "content": prompts.CHAT_SYSTEM},
                 {"role": "user", "content": instruction}], max_tokens=700)
            return {"respond_only": True, "respond_text": _text(ans)}
        return {"steps": clean, "cwd": None}

    def _verify(self, instruction: str, prior: list[dict]) -> dict:
        msgs = [{"role": "system", "content": prompts.VERIFIER_SYSTEM},
                {"role": "user",
                 "content": f"هدف کاربر:\n{instruction}\n\nنتایج واقعی اجرا:\n"
                            + json.dumps(prior[-12:], ensure_ascii=False, indent=1)}]
        try:
            return self.llm.chat_json(msgs, max_tokens=600)
        except Exception:
            # if verification itself fails, decide from execution results (honest fallback)
            all_ok = bool(prior) and all(p.get("ok") for p in prior)
            return {"pass": all_ok,
                    "summary": "تأییدکننده به دلیل خطای مدل پاسخ داد؛ بر اساس خروجی اجرا تصمیم گرفته شد.",
                    "issues": [] if all_ok else ["برخی گام‌ها ناموفق بودند"]}


def _text(completion: dict) -> str:
    try:
        return completion["choices"][0]["message"]["content"] or ""
    except Exception:
        return ""


class TaskWorker:
    """Single-threaded sequential worker (2-core CPU: one task at a time is honest)."""

    def __init__(self, engine: AgentEngine) -> None:
        self.engine = engine
        self.queue: list[str] = []
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

    def submit(self, task_id: str) -> None:
        with self.lock:
            self.queue.append(task_id)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True)
                self.thread.start()

    def _loop(self) -> None:
        while True:
            with self.lock:
                tid = self.queue.pop(0) if self.queue else None
            if tid is None:
                return
            db.update_task(tid, status="planning")
            self.engine.run_task(tid)

    @property
    def active(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


def parse_plan_json(text: str) -> dict:
    return parse_json_object(text)
