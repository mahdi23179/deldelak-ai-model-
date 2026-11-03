"""Shared fixtures for the Deldelak AI Model test suite.

Hard rules honoured here:
- NO model download, NO llama-server: `backend.main.llm` (an LLMClient instance)
  gets its methods replaced by a MockLLM; `backend.main.manager.start` is a no-op.
- Environment isolation: DELDELAK_STATE / DELDELAK_WORKSPACE (EXPORTS derives from
  WORKSPACE) point to tmp_path_factory dirs BEFORE any backend module is imported
  (backend.config reads env at import time).
- One session-scoped SQLite connection (backend.db._conn) is reused for the whole
  session; "restarts" are simulated with new TestClient lifecycles on the SAME app.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import pathlib
from typing import Any, Optional

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TERMINAL_STATUSES = {"completed", "completed_with_issues", "failed"}
MOCK_CHAT_TEXT = "سلام! من دلدلک هستم؛ این پاسخ از مدل ماکِ تست است."
MOCK_GREETING_TEXT = "سلام! من دلدلک هستم، خوبم؛ تو چطوری؟"


class MockLLM:
    """Replaces the methods of the backend.main.llm LLMClient instance.

    - health()       → async, returns state.healthy   (main.py awaits it)
    - chat()         → async, OpenAI-shaped dict      (main.py awaits it)
    - chat_json()    → SYNC, returns dict             (engine.py calls it WITHOUT await —
                       see known app bug; planner/verifier detected via system prompt)
    - stream_chat()  → async generator yielding SSE bytes + data: [DONE]
    """

    def __init__(self) -> None:
        self.healthy = True
        self.force_verifier_fail = False
        self.chat_calls: list[list[dict]] = []
        self.plan_calls: list[list[dict]] = []
        self.verify_calls: list[list[dict]] = []
        self.stream_calls: list[list[dict]] = []

    # ---------- low level ----------
    async def health(self) -> bool:
        return self.healthy

    async def aclose(self) -> None:  # lifespan shutdown calls this
        return None

    async def chat(self, messages: list[dict], **kwargs: Any) -> dict:
        self.chat_calls.append(messages)
        return {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 0,
            "model": "mock-llm",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": MOCK_CHAT_TEXT},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }

    def chat_json(self, messages: list[dict], **kwargs: Any) -> dict:
        """SYNC on purpose: backend/agent/engine.py calls it without `await`."""
        system = messages[0]["content"] if messages else ""
        if "وارس" in system:  # verifier prompt
            self.verify_calls.append(messages)
            return self._verdict(messages)
        assert "برنامه‌ریز" in system, "expected planner system prompt («برنامه‌ریز»)"
        self.plan_calls.append(messages)
        return self._plan(messages[-1]["content"])

    async def stream_chat(self, messages: list[dict], **kwargs: Any):
        self.stream_calls.append(messages)
        payload = json.dumps({"choices": [{"delta": {"content": "سلام"}}]},
                             ensure_ascii=False)
        yield ("data: " + payload + "\n\n").encode("utf-8")
        yield b"data: [DONE]\n\n"

    # ---------- verifier ----------
    def _verdict(self, messages: list[dict]) -> dict:
        if self.force_verifier_fail:
            return {"pass": False,
                    "summary": "هدف محقق نشد؛ برخی گام‌ها با خطا مواجه شدند.",
                    "issues": ["فایل موردنظر پیدا نشد"]}
        user = messages[-1]["content"] if messages else ""
        if "کاربر رد کرد" in user:
            return {"pass": True,
                    "summary": "گام حساس حذف فایل توسط کاربر رد شد؛ فایل حفظ و بقیه گام‌ها کامل شد."}
        return {"pass": True, "summary": "انجام شد"}

    # ---------- planner (deterministic, only real tools from tools.TOOLS) ----------
    def _plan(self, user: str) -> dict:
        if "سلام چطوری" in user:  # conversational → respond-only plan
            return {"text": MOCK_GREETING_TEXT}
        if "حذف کن" in user:  # sensitive flow: write a file then delete it
            m = re.search(r"([A-Za-z0-9._\-]+\.(?:txt|md|py|json))", user)
            fname = m.group(1) if m else "notes.txt"
            return {"steps": [
                {"title": "نوشتن فایل نمونه", "tool": "files.write",
                 "args": {"path": fname, "content": "متن نمونه برای آزمون حذف"}},
                {"title": "حذف فایل", "tool": "files.delete", "args": {"path": fname}},
            ]}
        if "missing-file" in user or "گم‌شده" in user:  # deterministic failing step
            return {"steps": [
                {"title": "خواندن فایل ناموجود", "tool": "files.read",
                 "args": {"path": "missing-file-xyz.txt"}},
            ]}
        # default: python project build+run+test
        m = re.search(r"(?:به نام|named)\s+([A-Za-z0-9_\-]+)", user)
        name = m.group(1) if m else "calc-agent"
        return {"steps": [
            {"title": "ساخت پروژه", "tool": "project.create",
             "args": {"name": name, "template": "python"}},
            {"title": "اجرای پروژه", "tool": "project.run", "args": {"name": name}},
            {"title": "تست پروژه", "tool": "project.test", "args": {"name": name}},
        ]}


class AppCtx:
    """Everything a test needs: authenticated client, key, modules, mock, helpers."""

    def __init__(self, client: Any, key: str, main: Any, config: Any, db: Any,
                 mock: MockLLM, workspace: pathlib.Path, state: pathlib.Path) -> None:
        self.client = client
        self.key = key
        self.main = main
        self.config = config
        self.db = db
        self.mock = mock
        self.workspace = workspace
        self.state = state

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"}

    def wait_task_status(self, task_id: str, statuses: set[str],
                         timeout: float = 30.0, interval: float = 0.2) -> dict:
        deadline = time.time() + timeout
        last: Optional[dict] = None
        while time.time() < deadline:
            r = self.client.get(f"/v1/agent/tasks/{task_id}", headers=self.headers)
            assert r.status_code == 200, f"poll failed: {r.status_code} {r.text}"
            last = r.json()
            if last.get("status") in statuses:
                return last
            time.sleep(interval)
        raise AssertionError(f"timeout ({timeout}s) waiting for {statuses}; last={last}")

    def wait_terminal(self, task_id: str, timeout: float = 30.0) -> dict:
        return self.wait_task_status(task_id, TERMINAL_STATUSES, timeout=timeout)

    def approve_task(self, task_id: str, approved: bool, timeout: float = 10.0):
        """POST approve; tolerates the tiny race where the engine has not yet
        registered its approval Event (409) by retrying briefly."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.client.post(f"/v1/agent/tasks/{task_id}/approve",
                                    json={"approved": approved}, headers=self.headers)
            if last.status_code == 200:
                return last
            time.sleep(0.1)
        return last


@pytest.fixture(scope="session")
def api(tmp_path_factory):
    """Session-scoped app with isolated tmp STATE/WORKSPACE dirs + MockLLM."""
    state_dir = tmp_path_factory.mktemp("deldelak-state")
    workspace_dir = tmp_path_factory.mktemp("deldelak-workspace")
    # exports dir derives from WORKSPACE (config.EXPORTS_DIR = WORKSPACE_DIR/exports)
    os.environ["DELDELAK_STATE"] = str(state_dir)
    os.environ["DELDELAK_WORKSPACE"] = str(workspace_dir)
    os.environ.pop("DELDELAK_EXPORTS", None)

    # backend imports happen ONLY now, after env setup
    import backend.config as config
    import backend.main as main
    import backend.db as db

    assert pathlib.Path(config.STATE_DIR) == state_dir, "config must read env at import"
    assert pathlib.Path(config.WORKSPACE_DIR) == workspace_dir
    assert pathlib.Path(config.EXPORTS_DIR) == workspace_dir / "exports"

    # ---- patch the LLM client instances used by main.py and AgentEngine ----
    mock = MockLLM()
    inst = main.llm                                   # async client: chat endpoints
    for name in ("health", "aclose", "chat", "stream_chat"):
        setattr(inst, name, getattr(mock, name))      # async members
    inst.chat_json = mock.chat_json                   # SYNC (engine calls it without await)

    # engine client is a SyncLLM in the worker thread → wrap async mock members
    import asyncio
    eng = main.engine_llm
    eng.chat = lambda *a, **k: asyncio.run(mock.chat(*a, **k))
    eng.chat_json = mock.chat_json
    eng.health = lambda *a, **k: True
    # never spawn llama-server, even if a binary/model happen to exist
    main.manager.start = lambda *a, **k: False

    # API key was generated at backend.main import time, inside the tmp STATE dir
    key_from_file = pathlib.Path(config.API_KEY_FILE).read_text(encoding="utf-8").strip()
    assert key_from_file == main.API_KEY
    assert config.DB_PATH == state_dir / "deldelak.db"

    ctx = AppCtx(client=None, key=main.API_KEY, main=main, config=config, db=db,
                 mock=mock, workspace=workspace_dir, state=state_dir)

    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:  # context manager → lifespan startup runs
        ctx.client = c
        yield ctx
