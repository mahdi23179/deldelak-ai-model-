"""Test 04 — Persistence: state survives TestClient restarts (same app, same SQLite)."""
from __future__ import annotations

import pathlib

from fastapi.testclient import TestClient


def test_state_survives_restart(api):
    app = api.main.app
    h = api.headers

    # ---------- first lifecycle ----------
    with TestClient(app) as c1:
        r = c1.post("/v1/sessions", json={"title": "ماندگاری", "mode": "chat"}, headers=h)
        assert r.status_code == 200
        sid = r.json()["id"]

        r = c1.post("/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "سلام"}],
                          "session_id": sid, "stream": False},
                    headers=h)
        assert r.status_code == 200

        r = c1.post("/v1/agent/tasks",
                    json={"instruction": "یک پروژه پایتون به نام persist-proj بساز و تست کن", "mode": "auto"},
                    headers=h)
        assert r.status_code == 200
        tid = r.json()["id"]

        task = api.wait_terminal(tid)  # worker runs in a background thread
        assert task["status"] == "completed"

    # c1 context exited → lifespan shutdown ran (llm.aclose / manager.stop are mocked no-ops)

    # ---------- second lifecycle: fresh client, SAME app + SAME db ----------
    with TestClient(app) as c2:
        # sessions persist
        sessions = c2.get("/v1/sessions", headers=h).json()["data"]
        assert any(s["id"] == sid and s["title"] == "ماندگاری" for s in sessions)

        # messages persist (user + assistant from the chat completion)
        r = c2.get(f"/v1/sessions/{sid}/messages", headers=h)
        assert r.status_code == 200
        assert [m["role"] for m in r.json()["data"]] == ["user", "assistant"]

        # task + steps persist with terminal status
        r = c2.get(f"/v1/agent/tasks/{tid}", headers=h)
        assert r.status_code == 200
        t = r.json()
        assert t["status"] == "completed"
        assert t["steps"] != []
        assert t["result"] is not None

        # task also appears in the list endpoint
        listed = c2.get("/v1/agent/tasks", headers=h).json()["data"]
        assert any(x["id"] == tid and x["status"] == "completed" for x in listed)


def test_api_key_stable_across_restart(api):
    key_file = pathlib.Path(api.config.API_KEY_FILE)
    before = key_file.read_text(encoding="utf-8").strip()
    assert before == api.key

    with TestClient(api.main.app) as c:  # a "restart" of the same app
        # same key still authorizes…
        r = c.get("/v1/models", headers={"Authorization": f"Bearer {before}"})
        assert r.status_code == 200
        assert r.json()["data"][0]["ready"] is True
        # …and a stale/wrong key is still rejected
        r = c.get("/v1/models", headers={"Authorization": "Bearer ddk-stale-key"})
        assert r.status_code == 401

    after = key_file.read_text(encoding="utf-8").strip()
    assert after == before, "API key file must not change across restarts"
