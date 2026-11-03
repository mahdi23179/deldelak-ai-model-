"""Test 02 — Agent engine end-to-end with the MockLLM (planner/verifier/executor)."""
from __future__ import annotations

import json

import pytest


def test_agent_full_task_create_project(api):
    r = api.client.post("/v1/agent/tasks",
                        json={"instruction": "یک پروژه پایتون به نام calc-basit بساز و تست کن",
                              "mode": "auto"},
                        headers=api.headers)
    assert r.status_code == 200
    created = r.json()
    assert created["status"] == "queued"
    tid = created["id"]

    task = api.wait_terminal(tid)
    assert task["status"] == "completed", task

    by_tool = {s["tool"]: s for s in task["steps"]}
    assert "project.create" in by_tool and by_tool["project.create"]["status"] == "ok"
    assert "project.test" in by_tool and by_tool["project.test"]["status"] == "ok"

    pdir = api.workspace / "calc-basit"
    assert pdir.is_dir(), "project dir must exist in the workspace"
    assert (pdir / "main.py").is_file()

    # a test_results row must have been recorded by project.test (task-scoped ctx)
    rows = api.db.conn().execute(
        "SELECT * FROM test_results WHERE project_name=?", ("calc-basit",)).fetchall()
    assert len(rows) >= 1
    assert rows[-1]["task_id"] == tid

    # result is a JSON string: {"summary": ..., "verifier": {...}}
    result = json.loads(task["result"])
    assert result["summary"]
    assert result["verifier"]["pass"] is True
    assert task["checkpoints"] >= 1


def test_agent_sensitive_delete_denied(api):
    fname = "sample-deny.txt"
    r = api.client.post("/v1/agent/tasks",
                        json={"instruction": f"فایل {fname} را حذف کن", "mode": "auto"},
                        headers=api.headers)
    assert r.status_code == 200
    tid = r.json()["id"]

    task = api.wait_task_status(tid, {"awaiting_approval"})
    assert task["status"] == "awaiting_approval"

    resp = api.approve_task(tid, approved=False)
    assert resp is not None and resp.status_code == 200
    assert resp.json() == {"ok": True, "approved": False}

    task = api.wait_terminal(tid)
    assert task["status"] == "completed"

    del_steps = [s for s in task["steps"] if s["tool"] == "files.delete"]
    assert len(del_steps) == 1
    step = del_steps[0]
    assert step["status"] != "ok"           # skipped — never executed
    assert "حساس" in step["output"]         # the sensitive-operation notice
    assert step["finished_at"] is not None

    result = json.loads(task["result"])
    assert "رد" in result["summary"]        # verifier acknowledged the denial

    # the target file must still exist
    assert (api.workspace / fname).is_file()


def test_agent_sensitive_delete_approved(api):
    fname = "sample-approve.txt"
    (api.workspace / fname).write_text("قدیمی", encoding="utf-8")  # pre-create target
    r = api.client.post("/v1/agent/tasks",
                        json={"instruction": f"فایل {fname} را حذف کن", "mode": "auto"},
                        headers=api.headers)
    assert r.status_code == 200
    tid = r.json()["id"]

    api.wait_task_status(tid, {"awaiting_approval"})
    resp = api.approve_task(tid, approved=True)
    assert resp is not None and resp.status_code == 200

    task = api.wait_terminal(tid)
    assert task["status"] == "completed"

    step = [s for s in task["steps"] if s["tool"] == "files.delete"][0]
    assert step["status"] == "ok"
    assert "حذف شد" in step["output"]
    assert not (api.workspace / fname).exists(), "approved delete must remove the file"


def test_agent_task_not_found(api):
    r = api.client.get("/v1/agent/tasks/nonexistent-id", headers=api.headers)
    assert r.status_code == 404
    assert "تسک پیدا نشد" in r.json()["detail"]


def test_agent_approve_not_waiting_conflict(api):
    # a quick conversational task completes without ever waiting for approval
    r = api.client.post("/v1/agent/tasks",
                        json={"instruction": "سلام چطوری؟ (تسک سریع برای آزمون ۴۰۹)",
                              "mode": "auto"},
                        headers=api.headers)
    tid = r.json()["id"]
    task = api.wait_terminal(tid)
    assert task["status"] == "completed"

    resp = api.client.post(f"/v1/agent/tasks/{tid}/approve",
                           json={"approved": True}, headers=api.headers)
    assert resp.status_code == 409
    assert "انتظار تأیید" in resp.json()["detail"]


def test_respond_tool_conversational(api):
    r = api.client.post("/v1/agent/tasks",
                        json={"instruction": "سلام چطوری؟", "mode": "auto"},
                        headers=api.headers)
    assert r.status_code == 200
    tid = r.json()["id"]

    task = api.wait_terminal(tid)
    assert task["status"] == "completed"
    assert task["steps"] == []              # respond-only: no tool steps at all
    result = json.loads(task["result"])
    assert "دلدلک" in result["summary"]     # summary contains the respond text


def test_agent_verifier_failure_replan_bounded(api):
    """Verifier keeps saying pass=false → bounded repair cycles → completed_with_issues."""
    api.mock.force_verifier_fail = True
    try:
        r = api.client.post("/v1/agent/tasks",
                            json={"instruction": "فایل گم‌شده missing-file-xyz.txt را بخوان",
                                  "mode": "auto"},
                            headers=api.headers)
        tid = r.json()["id"]
        task = api.wait_terminal(tid, timeout=60)
        assert task["status"] == "completed_with_issues"
        assert task["cycles"] == api.config.AGENT_MAX_CYCLES
        assert task["steps"], "each repair cycle should leave one failed step"
        assert all(s["status"] == "error" for s in task["steps"])
        result = json.loads(task["result"])
        assert "محقق نشد" in result["summary"]
    finally:
        api.mock.force_verifier_fail = False
