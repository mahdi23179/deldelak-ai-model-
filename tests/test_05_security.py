"""Test 05 - security hardening regressions (mission §3, §6, §7):
path jail, env scrubbing, process-tree kill, rate limiting, OAuth owner
recovery (mocked GitHub), internal can-stop, key fingerprint push.
"""
from __future__ import annotations

import os
import time

import pytest


# ---------------- path jail (audit A3) ----------------

def test_safe_path_blocks_prefix_confusion(api):
    from backend.agent.tools import safe_path, ToolError
    base = api.workspace
    evil_base = base.parent / (base.name + "-evil")
    # a sibling directory sharing the prefix must NOT pass the jail
    with pytest.raises(ToolError):
        safe_path(f"../{evil_base.name}/x")


def test_ws_path_blocks_sibling_prefix(api):
    # /v1/files with a path that resolves to a sibling dir must be rejected
    sibling = api.workspace.parent / (api.workspace.name + "-evil")
    r = api.client.get("/v1/files/content", params={"path": f"../{sibling.name}/secret.txt"},
                       headers=api.headers)
    assert r.status_code == 400


def test_safe_path_allows_normal_paths(api):
    from backend.agent.tools import safe_path
    p = safe_path("sub/dir/file.txt")
    assert str(p).startswith(str(api.workspace))


# ---------------- env scrubbing (audit A5) ----------------

def test_child_env_never_contains_tokens(api, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_super_secret_value")
    monkeypatch.setenv("WAKE_TOKEN", "pat_leaky_value")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))
    env = api.config.child_env()
    assert "GH_TOKEN" not in env
    assert "WAKE_TOKEN" not in env
    assert "PATH" in env  # allowlisted essentials survive


def test_terminal_execute_cannot_read_github_token(api, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_super_secret_value")
    res = api.main.__dict__  # touch import
    from backend.agent import tools
    out = tools.dispatch("terminal.execute",
                         {"command": 'echo "val=${GH_TOKEN:-EMPTY}"'}, {"task_id": None})
    assert out["ok"] is True
    assert "val=EMPTY" in out["output"], "child must not inherit GH_TOKEN"
    assert "ghp_super_secret_value" not in out["output"]


# ---------------- process tree kill + timeout (audit A6) ----------------

def test_terminal_timeout_kills_whole_tree(api):
    from backend.agent import tools
    t0 = time.time()
    res = tools.dispatch("terminal.execute",
                         {"command": "sleep 25 & sleep 25", "timeout": 1},
                         {"task_id": None})
    elapsed = time.time() - t0
    assert res["exit_code"] == 124, res
    assert elapsed < 10, f"timeout must kill the tree fast, took {elapsed:.1f}s"
    assert "timeout" in res["output"].lower()


def test_sensitive_command_requires_approval(api):
    from backend.agent import tools
    with pytest.raises(Exception):
        tools.dispatch("terminal.execute", {"command": "sudo id"}, {"task_id": None})


# ---------------- rate limiting (audit A12) ----------------

def test_rate_limiter_sliding_window(api):
    from backend import ratelimit
    ratelimit.reset_for_tests()
    results = [ratelimit.check("key_claim", "1.2.3.4") for _ in range(12)]
    # policy: 10 per 60s
    assert all(ok for ok, _ in results[:10])
    assert not results[10][0], "11th hit must be limited"
    assert results[10][1] >= 1, "retry-after seconds must be positive"
    ratelimit.reset_for_tests()


# ---------------- OAuth owner recovery (mission §3) ----------------

@pytest.fixture()
def oauth_env(api, monkeypatch):
    monkeypatch.setattr(api.config, "OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(api.config, "OAUTH_CLIENT_SECRET", "test-secret")
    return api


def test_get_key_returns_recovery_instructions(oauth_env):
    r = oauth_env.client.get("/api/key")
    assert r.status_code == 401
    d = r.json()
    assert d["owner"] == oauth_env.config.OWNER_LOGIN
    assert d["recovery_url"] == "/api/key/oauth/start"
    assert oauth_env.key not in r.text


def test_oauth_start_redirects_with_state_cookie(oauth_env):
    r = oauth_env.client.get("/api/key/oauth/start", follow_redirects=False)
    assert r.status_code == 302
    assert "github.com/login/oauth/authorize" in r.headers["location"]
    assert "scope=read%3Auser" in r.headers["location"] or "scope=read:user" in r.headers["location"]
    assert r.headers["location"].count("state=") == 1


def test_oauth_callback_rejects_bad_state(oauth_env):
    r = oauth_env.client.get("/api/key/oauth/callback",
                             params={"code": "c", "state": "forged"})
    assert r.status_code == 400


def test_oauth_full_flow_owner_can_reveal(oauth_env, monkeypatch):
    # mock GitHub: token exchange + user fetch (network is never touched)
    monkeypatch.setattr("backend.keys.exchange_code", lambda code: "one-time-token")
    monkeypatch.setattr("backend.keys.fetch_login", lambda tok: oauth_env.config.OWNER_LOGIN)
    r = oauth_env.client.get("/api/key/oauth/start", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    state = loc.split("state=")[1].split("&")[0]
    cb = oauth_env.client.get("/api/key/oauth/callback",
                              params={"code": "abc", "state": state}, follow_redirects=False)
    assert cb.status_code == 302
    assert cb.headers["location"].startswith("/?owner_session=1")
    # session cookie must be set HttpOnly (never readable by JS)
    setc = cb.headers.get("set-cookie", "")
    assert "ddk_session=" in setc and "httponly" in setc.lower()
    # now reveal works with the owner session + CSRF header
    rev = oauth_env.client.post("/api/key/reveal", headers={"X-Requested-With": "XMLHttpRequest"})
    assert rev.status_code == 200
    assert rev.json()["key"] == oauth_env.key


def test_oauth_non_owner_gets_403(oauth_env, monkeypatch):
    oauth_env.client.cookies.clear()  # drop any owner session from earlier tests
    monkeypatch.setattr("backend.keys.exchange_code", lambda code: "tok")
    monkeypatch.setattr("backend.keys.fetch_login", lambda tok: "not-the-owner")
    r = oauth_env.client.get("/api/key/oauth/start", follow_redirects=False)
    state = r.headers["location"].split("state=")[1].split("&")[0]
    cb = oauth_env.client.get("/api/key/oauth/callback",
                              params={"code": "abc", "state": state}, follow_redirects=False)
    assert cb.status_code == 403
    assert oauth_env.key not in cb.text
    # and no owner session was issued
    sess = oauth_env.client.get("/api/key/session").json()
    assert sess["is_owner"] is False


def test_reveal_without_csrf_header_rejected(oauth_env, monkeypatch):
    oauth_env.client.cookies.clear()
    monkeypatch.setattr("backend.keys.exchange_code", lambda code: "tok")
    monkeypatch.setattr("backend.keys.fetch_login", lambda tok: oauth_env.config.OWNER_LOGIN)
    r = oauth_env.client.get("/api/key/oauth/start", follow_redirects=False)
    state = r.headers["location"].split("state=")[1].split("&")[0]
    oauth_env.client.get("/api/key/oauth/callback", params={"code": "abc", "state": state},
                         follow_redirects=False)
    rev = oauth_env.client.post("/api/key/reveal")  # no X-Requested-With
    assert rev.status_code == 403


# ---------------- session forging (mission §3) ----------------

def test_owner_session_cannot_be_forged(oauth_env):
    r = oauth_env.client.get("/api/key/session", cookies={"ddk_session": "forged.sig"})
    assert r.json()["authenticated"] is False
    rev = oauth_env.client.post("/api/key/reveal",
                                headers={"X-Requested-With": "X"}, cookies={"ddk_session": "forged.sig"})
    assert rev.status_code == 401


# ---------------- internal can-stop (mission §5) ----------------

def test_can_stop_requires_shared_secret(api, monkeypatch):
    monkeypatch.setattr(api.config, "GATEWAY_SHARED_SECRET", "test-gw-secret")
    r = api.client.get("/internal/can-stop")
    assert r.status_code == 403
    r2 = api.client.get("/internal/can-stop", headers={"X-Gateway-Secret": "wrong"})
    assert r2.status_code == 403
    r3 = api.client.get("/internal/can-stop", headers={"X-Gateway-Secret": "test-gw-secret"})
    assert r3.status_code == 200
    d = r3.json()
    for k in ("busy", "active_requests", "agent_busy", "model_loading", "reason"):
        assert k in d
    assert d["busy"] is False


def test_can_stop_reports_busy_with_inflight(api, monkeypatch):
    monkeypatch.setattr(api.config, "GATEWAY_SHARED_SECRET", "test-gw-secret")
    api.main._inflight_change(+1)
    try:
        r = api.client.get("/internal/can-stop", headers={"X-Gateway-Secret": "test-gw-secret"})
        assert r.json()["busy"] is True
        assert r.json()["active_requests"] >= 1
    finally:
        api.main._inflight_change(-1)


# ---------------- health honesty (mission §5) ----------------

def test_health_loading_model_state(api, monkeypatch):
    monkeypatch.setattr(api.main.manager, "is_loading", lambda: True)
    monkeypatch.setattr(api.main.manager, "is_running", lambda: False)
    r = api.client.get("/health")
    assert r.json()["status"] == "loading_model"
    assert r.json()["model_loading"] is True
    # API must NOT declare READY while loading
    assert r.json()["status"] != "ready"
    monkeypatch.setattr(api.main.manager, "is_loading", lambda: False)


# ---------------- key fingerprint push to gateway ----------------

def test_fingerprint_push_never_sends_key_material(api, monkeypatch):
    calls = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        calls["headers"] = headers
        class R:
            status_code = 200
        return R()
    monkeypatch.setattr("backend.main.httpx.post", fake_post)
    monkeypatch.setattr(api.config, "GATEWAY_URL", "https://gw.test")
    monkeypatch.setattr(api.config, "GATEWAY_SHARED_SECRET", "sec")
    api.main._push_fingerprint("ddk-never-logged")
    assert calls["url"] == "https://gw.test/internal/key-fingerprint"
    assert calls["json"] == {"key_fingerprint": "sha256:" + __import__("hashlib").sha256(b"ddk-never-logged").hexdigest()}
    # the raw key must never appear in the payload
    assert "ddk-never-logged" not in str(calls["json"])


# ---------------- audit log (mission §6) ----------------

def test_tool_audit_log_written_without_secrets(api, monkeypatch):
    from backend.agent import tools
    tools.dispatch("terminal.execute", {"command": "echo hello-audit"}, {"task_id": "t1"})
    log = api.config.AUDIT_LOG_FILE
    assert log.exists()
    content = log.read_text(encoding="utf-8")
    assert "terminal.execute" in content
    assert "echo hello-audit" in content  # command is auditable (not a secret)
