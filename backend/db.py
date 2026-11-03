"""Deldelak AI Model - SQLite persistence (sessions, tasks, steps, projects, checkpoints).

Everything survives restarts: chat history, agent task state, project plans,
errors and test results. Uses WAL mode for safe concurrent access.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

from . import config

_lock = threading.RLock()  # reentrant: conn() may be called while the lock is held
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT 'گفتگوی جدید',
  mode TEXT NOT NULL DEFAULT 'chat',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at REAL NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  instruction TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'auto',
  status TEXT NOT NULL DEFAULT 'queued',
  result TEXT,
  error TEXT,
  cycles INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS task_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  phase TEXT NOT NULL,
  seq INTEGER NOT NULL DEFAULT 0,
  tool TEXT,
  title TEXT,
  args TEXT,
  output TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  started_at REAL,
  finished_at REAL,
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  path TEXT NOT NULL,
  template TEXT NOT NULL DEFAULT 'python',
  status TEXT NOT NULL DEFAULT 'created',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  data TEXT NOT NULL,
  created_at REAL NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);
CREATE TABLE IF NOT EXISTS errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  scope TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS test_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  project_name TEXT,
  passed INTEGER,
  failed INTEGER,
  detail TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_steps_task ON task_steps(task_id);
"""


def init_db() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            return
        config.ensure_dirs()
        _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=5000")
        _conn.executescript(SCHEMA)
        _conn.commit()


def conn() -> sqlite3.Connection:
    init_db()
    return _conn  # type: ignore[return-value]


def _now() -> float:
    return time.time()


# ---------- sessions ----------

def create_session(title: str = "گفتگوی جدید", mode: str = "chat") -> dict:
    sid = uuid.uuid4().hex[:12]
    with _lock:
        conn().execute(
            "INSERT INTO sessions(id,title,mode,created_at) VALUES(?,?,?,?)",
            (sid, title or "گفتگوی جدید", mode, _now()),
        )
        conn().commit()
    return {"id": sid, "title": title or "گفتگوی جدید", "mode": mode, "created_at": _now()}


def list_sessions() -> list[dict]:
    with _lock:
        rows = conn().execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_session(sid: str) -> Optional[dict]:
    with _lock:
        row = conn().execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    return dict(row) if row else None


def delete_session(sid: str) -> None:
    with _lock:
        conn().execute("DELETE FROM messages WHERE session_id=?", (sid,))
        conn().execute("DELETE FROM sessions WHERE id=?", (sid,))
        conn().commit()


def add_message(session_id: str, role: str, content: str) -> dict:
    with _lock:
        cur = conn().execute(
            "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
            (session_id, role, content, _now()),
        )
        conn().commit()
        mid = cur.lastrowid
    return {"id": mid, "session_id": session_id, "role": role, "content": content, "created_at": _now()}


def list_messages(session_id: str) -> list[dict]:
    with _lock:
        rows = conn().execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id ASC", (session_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- tasks ----------

def create_task(instruction: str, mode: str = "auto") -> dict:
    tid = uuid.uuid4().hex[:12]
    now = _now()
    with _lock:
        conn().execute(
            "INSERT INTO tasks(id,instruction,mode,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (tid, instruction, mode, "queued", now, now),
        )
        conn().commit()
    return {"id": tid, "instruction": instruction, "mode": mode, "status": "queued"}


def update_task(task_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [_now(), task_id]
    with _lock:
        conn().execute(f"UPDATE tasks SET {cols}, updated_at=? WHERE id=?", vals)
        conn().commit()


def get_task(task_id: str) -> Optional[dict]:
    with _lock:
        row = conn().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        t = dict(row)
        t["steps"] = list_steps(task_id)
        return t


def list_tasks() -> list[dict]:
    with _lock:
        rows = conn().execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 100").fetchall()
        out = []
        for r in rows:
            t = dict(r)
            steps = list_steps(t["id"])
            t["steps_count"] = len(steps)
            out.append(t)
        return out


def add_step(task_id: str, phase: str, seq: int, tool: str | None, title: str | None,
             args: dict | None, status: str = "pending") -> int:
    with _lock:
        cur = conn().execute(
            "INSERT INTO task_steps(task_id,phase,seq,tool,title,args,output,status,started_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (task_id, phase, seq, tool, title, json.dumps(args or {}, ensure_ascii=False),
             None, status, _now()),
        )
        conn().commit()
        return int(cur.lastrowid)


def finish_step(step_id: int, status: str, output: str | None) -> None:
    with _lock:
        conn().execute(
            "UPDATE task_steps SET status=?, output=?, finished_at=? WHERE id=?",
            (status, output, _now(), step_id),
        )
        conn().commit()


def list_steps(task_id: str) -> list[dict]:
    with _lock:
        rows = conn().execute(
            "SELECT * FROM task_steps WHERE task_id=? ORDER BY id ASC", (task_id,)
        ).fetchall()
    out = []
    for r in rows:
        s = dict(r)
        try:
            s["args"] = json.loads(s.get("args") or "{}")
        except Exception:
            s["args"] = {}
        out.append(s)
    return out


def save_checkpoint(task_id: str, data: dict) -> None:
    with _lock:
        conn().execute(
            "INSERT INTO checkpoints(task_id,data,created_at) VALUES(?,?,?)",
            (task_id, json.dumps(data, ensure_ascii=False), _now()),
        )
        conn().commit()


def list_checkpoints(task_id: str) -> list[dict]:
    with _lock:
        rows = conn().execute(
            "SELECT * FROM checkpoints WHERE task_id=? ORDER BY id ASC", (task_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def log_error(task_id: str | None, scope: str, message: str) -> None:
    with _lock:
        conn().execute(
            "INSERT INTO errors(task_id,scope,message,created_at) VALUES(?,?,?,?)",
            (task_id, scope, message[:4000], _now()),
        )
        conn().commit()


def save_test_result(task_id: str | None, project_name: str, passed: int, failed: int, detail: str) -> None:
    with _lock:
        conn().execute(
            "INSERT INTO test_results(task_id,project_name,passed,failed,detail,created_at) VALUES(?,?,?,?,?,?)",
            (task_id, project_name, passed, failed, detail[:4000], _now()),
        )
        conn().commit()


def recover_interrupted_tasks() -> int:
    """On startup: tasks that were mid-flight are marked failed (honest restart),
    queued tasks are kept queued (worker resumes them)."""
    with _lock:
        c = conn()
        n = 0
        for st in ("planning", "executing", "verifying", "awaiting_approval"):
            cur = c.execute(
                "UPDATE tasks SET status='failed', error='سیستم در میانه اجرای این تسک ری‌استارت شده است (interrupted). دوباره ارسال کنید.', updated_at=? WHERE status=?",
                (_now(), st),
            )
            n += cur.rowcount
        c.commit()
        return n


# ---------- projects ----------

def create_project(name: str, path: str, template: str = "python") -> dict:
    pid = uuid.uuid4().hex[:12]
    with _lock:
        conn().execute(
            "INSERT INTO projects(id,name,path,template,status,created_at) VALUES(?,?,?,?,?,?)",
            (pid, name, path, template, "created", _now()),
        )
        conn().commit()
    return {"id": pid, "name": name, "path": path, "template": template, "status": "created"}


def get_project_by_name(name: str) -> Optional[dict]:
    with _lock:
        row = conn().execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
    return dict(row) if row else None


def get_project(pid: str) -> Optional[dict]:
    with _lock:
        row = conn().execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else None


def list_projects() -> list[dict]:
    with _lock:
        rows = conn().execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        p = dict(r)
        ppath = pathlib.Path(p["path"])
        p["files_count"] = sum(1 for _ in ppath.rglob("*") if _.is_file()) if ppath.is_dir() else 0
        out.append(p)
    return out


def update_project_status(pid: str, status: str) -> None:
    with _lock:
        conn().execute("UPDATE projects SET status=? WHERE id=?", (status, pid))
        conn().commit()
