"""Test 01 — API basics: health, auth, models, chat completions, key claim, system status."""
from __future__ import annotations

import pathlib


def test_health_public_no_auth(api):
    r = api.client.get("/health")  # no Authorization header
    assert r.status_code == 200
    d = r.json()
    assert d["service"] == "deldelak-ai-model"
    for k in ("status", "model_loaded", "model_loading", "active_requests",
              "queued_requests", "uptime_s", "last_activity", "last_error", "backend_ready"):
        assert k in d, f"missing {k}"
    assert isinstance(d["uptime_s"], (int, float))
    assert isinstance(d["model_loaded"], bool)
    assert isinstance(d["active_requests"], int)
    assert d["status"] in ("ok", "degraded", "loading_model", "busy")


def test_models_requires_auth(api):
    r = api.client.get("/v1/models")
    assert r.status_code == 401
    assert "کلید" in r.json()["detail"]  # Persian error


def test_models_wrong_key(api):
    r = api.client.get("/v1/models", headers={"Authorization": "Bearer ddk-wrong-key"})
    assert r.status_code == 401
    r2 = api.client.get("/v1/models", headers={"X-API-Key": "ddk-also-wrong"})
    assert r2.status_code == 401


def test_models_with_auth(api):
    r = api.client.get("/v1/models", headers=api.headers)
    assert r.status_code == 200
    d = r.json()
    assert d["object"] == "list"
    m = d["data"][0]
    assert m["id"] == "qwen2.5-3b-instruct-q4-k-m"
    assert m["ready"] is True  # mock health() returns True
    assert m["owned_by"] == "deldelak-local"


def test_chat_completion_non_stream(api):
    payload = {"model": "gpt-4-turbo",  # must be normalized to the local model id
               "messages": [{"role": "user", "content": "سلام"}],
               "max_tokens": 64,
               "stream": False}
    r = api.client.post("/v1/chat/completions", json=payload, headers=api.headers)
    assert r.status_code == 200
    d = r.json()
    content = d["choices"][0]["message"]["content"]
    assert isinstance(content, str) and content.startswith("سلام")
    assert d["model"] == api.config.MODEL_ID  # normalized
    assert d["choices"][0]["message"]["role"] == "assistant"


def test_chat_completion_stream(api):
    payload = {"messages": [{"role": "user", "content": "سلام"}], "stream": True}
    r = api.client.post("/v1/chat/completions", json=payload, headers=api.headers)
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    body = r.text
    assert "سلام" in body            # the delta text passes through
    assert "data: [DONE]" in body    # terminator present
    assert len(api.mock.stream_calls) == 1


def test_chat_not_ready_503(api):
    api.mock.healthy = False
    try:
        r = api.client.post("/v1/chat/completions",
                            json={"messages": [{"role": "user", "content": "سلام"}]},
                            headers=api.headers)
        assert r.status_code == 503
        detail = r.json()["detail"]
        assert "آماده نیست" in detail  # Persian "model not ready" message
    finally:
        api.mock.healthy = True  # restore for the rest of the session
    # and it works again immediately
    r2 = api.client.post("/v1/chat/completions",
                         json={"messages": [{"role": "user", "content": "سلام"}]},
                         headers=api.headers)
    assert r2.status_code == 200


def test_api_key_never_served_to_first_caller(api):
    """v2 security: the first-come claim window is REMOVED. GET /api/key must
    never return key material to an unauthenticated caller (owner-only OAuth)."""
    r = api.client.get("/api/key")
    assert r.status_code == 401
    d = r.json()
    assert "key" not in d or d.get("key") != api.key
    assert d["error"] == "owner_session_required"
    assert d["recovery_url"].startswith("/api/key/oauth")
    # and it still does not leak after repeated hits
    for _ in range(3):
        r2 = api.client.get("/api/key")
        assert r2.status_code in (401, 429)
        body = r2.text
        assert api.key not in body  # key material never appears


def test_api_key_reveal_requires_owner_session(api):
    """Reveal needs the owner session cookie (or valid key) + CSRF header."""
    r = api.client.post("/api/key/reveal")  # no session, no CSRF header
    assert r.status_code == 401
    # a valid API key alone IS accepted for automation (Bearer header)
    r2 = api.client.post("/api/key/reveal", headers={"Authorization": f"Bearer {api.key}",
                                                    "X-Requested-With": "XMLHttpRequest"})
    assert r2.status_code == 200
    assert r2.json()["key"] == api.key
    assert api.key not in str(api.client.get("/health").text)  # never leaks via health


def test_api_key_rotate_revokes_old_key(api):
    """Rotating must invalidate the previous key immediately (mission §3.8)."""
    old = api.key
    r = api.client.post("/api/key/rotate", headers={"Authorization": f"Bearer {old}",
                                                   "X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 200
    new = r.json()["key"]
    assert new != old and new == api.main.API_KEY
    # old key is dead
    r_old = api.client.get("/v1/models", headers={"Authorization": f"Bearer {old}"})
    assert r_old.status_code == 401
    # new key works
    r_new = api.client.get("/v1/models", headers={"Authorization": f"Bearer {new}"})
    assert r_new.status_code == 200
    api.key = new  # keep the session-scoped fixture usable
    api.state.joinpath("api_key").write_text(new)  # keep .state file consistent


def test_system_status_fields(api):
    r = api.client.get("/v1/system/status", headers=api.headers)
    assert r.status_code == 200
    d = r.json()
    for k in ("ram", "disk", "model", "llm", "agent", "platform", "version", "uptime_s"):
        assert k in d, f"missing {k}"
    assert isinstance(d["ram"]["percent"], (int, float))
    assert isinstance(d["disk"]["percent"], (int, float))
    assert d["model"]["id"] == api.config.MODEL_ID
    assert "host" in d["llm"] and "port" in d["llm"]
    assert "worker_alive" in d["agent"] and "active_tasks" in d["agent"]
