"""Test 01 — API basics: health, auth, models, chat completions, key claim, system status."""
from __future__ import annotations

import pathlib


def test_health_public_no_auth(api):
    r = api.client.get("/health")  # no Authorization header
    assert r.status_code == 200
    d = r.json()
    assert d["service"] == "deldelak-ai-model"
    for k in ("status", "model_loaded", "uptime_s"):
        assert k in d, f"missing {k}"
    assert isinstance(d["uptime_s"], (int, float))
    assert isinstance(d["model_loaded"], bool)
    assert d["status"] in ("ok", "degraded")


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


def test_api_key_claim_once(api):
    # Deterministic claim window: no other test uses GET /api/key, so removing the
    # claim marker (inside the isolated tmp STATE dir only) is safe here.
    claimed_file = pathlib.Path(api.config.KEY_CLAIMED_FILE)
    if claimed_file.exists():
        claimed_file.unlink()
    r1 = api.client.get("/api/key")
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["claimed"] is True
    assert d1["key"] == api.key  # matches backend.main.API_KEY / API_KEY_FILE
    r2 = api.client.get("/api/key")
    assert r2.status_code == 403
    assert "ادعا" in r2.json()["detail"] or "دریافت" in r2.json()["detail"]


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
