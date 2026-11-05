"""Deldelak AI Model - owner-only API key recovery (GitHub OAuth).

Threat model / design:
- The API key lives in `.state/api_key` (mode 600, git-ignored). It is never
  served to a first-come-first-served caller any more.
- Only the GitHub account `config.OWNER_LOGIN` may reveal/rotate/revoke the key.
- Identity is proven through the standard GitHub OAuth *authorization code*
  flow: browser -> GitHub consent -> callback -> server exchanges the code for
  an access token (used ONCE to read `GET /user`, then discarded - never
  persisted, never logged, never sent to the browser).
- The OAuth `state` parameter is a random nonce stored in a short-lived
  HttpOnly cookie (double-submit) - CSRF protection for the flow itself.
- After owner verification the server issues a signed, short-lived (15 min)
  session cookie: `base64url(payload).base64url(HMAC-SHA256)`. The signing key
  is generated once and stored in `.state/session_secret` (mode 600).
- OAuth secrets live ONLY in Codespace secrets (env) - never in the repo,
  never in JS, never in logs. Key material is masked everywhere.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from . import config, ratelimit

OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
OAUTH_USER_URL = "https://api.github.com/user"

SESSION_TTL_S = 15 * 60
STATE_TTL_S = 10 * 60

# in-memory OAuth state store: nonce -> expiry (state also doubles as CSRF token)
_oauth_states: Dict[str, float] = {}


# ---------------------------------------------------------------- state file

def _session_secret_file():
    return config.STATE_DIR / "session_secret"


def session_secret() -> bytes:
    """Stable HMAC key for owner sessions (created once, 600 perms)."""
    f = _session_secret_file()
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(secrets.token_bytes(32))
        os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)
    return f.read_bytes()


# ---------------------------------------------------------------- helpers

def mask_secret(value: str, keep: int = 4) -> str:
    """Safe-for-logs representation: `ddk-…ab12 (sha256:1a2b3c4d)`."""
    if not value:
        return "(empty)"
    fp = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"****{value[-keep:]} (sha256:{fp})"


def fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


# ---------------------------------------------------------------- sessions

def issue_session(login: str) -> Tuple[str, int]:
    payload = {"login": login, "exp": int(time.time()) + SESSION_TTL_S, "nonce": secrets.token_hex(8)}
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(session_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}", SESSION_TTL_S


def verify_session(token: str) -> Optional[str]:
    """Return the login if the signed session is valid and unexpired."""
    if not token or "." not in token:
        return None
    body, _, sig = token.rpartition(".")
    try:
        expected = hmac.new(session_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64url_decode(body))
        if int(payload.get("exp", 0)) < time.time():
            return None
        return str(payload.get("login") or "") or None
    except Exception:
        return None


def is_owner_session(token: str) -> bool:
    return verify_session(token) == config.OWNER_LOGIN


# ---------------------------------------------------------------- oauth flow

def oauth_configured() -> bool:
    return bool(config.OAUTH_CLIENT_ID and config.OAUTH_CLIENT_SECRET)


def oauth_start(csrf_random: str) -> str:
    """Create a signed state nonce, store it, and return the GitHub authorize URL."""
    nonce = secrets.token_urlsafe(24)
    with _lock_states():
        _cleanup_states_locked()
        _oauth_states[nonce] = time.time() + STATE_TTL_S
    # the nonce binds to the HttpOnly state cookie value (double-submit)
    payload = f"{nonce}.{csrf_random}"
    params = {
        "client_id": config.OAUTH_CLIENT_ID,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "scope": "read:user",
        "state": _b64url(payload.encode("utf-8")),
        "allow_signup": "false",
    }
    return f"{OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _lock_states():  # tiny helper keeps the module readable
    import threading
    return _StatesLock()


class _StatesLock:
    def __enter__(self):
        _states_lock.acquire()
        return self

    def __exit__(self, *exc):
        _states_lock.release()


import threading  # noqa: E402  (placed after helpers for readability)
_states_lock = threading.Lock()


def _cleanup_states_locked() -> None:
    now = time.time()
    for k in [k for k, exp in _oauth_states.items() if exp < now]:
        _oauth_states.pop(k, None)


def oauth_consume_state(state_param: str, csrf_random: str) -> bool:
    """Validate + consume the OAuth state (CSRF + replay protection)."""
    try:
        decoded = _b64url_decode(state_param).decode("utf-8")
        nonce, _, bound = decoded.partition(".")
        if not nonce or bound != csrf_random:
            return False
        with _states_lock:
            exp = _oauth_states.pop(nonce, None)
            _cleanup_states_locked()
        return exp is not None and exp >= time.time()
    except Exception:
        return False


# ---------------------------------------------------------------- github io

def exchange_code(code: str) -> Optional[str]:
    """Exchange the auth code for a GitHub access token. Token is used once and
    never stored. Returns the access token or None."""
    import urllib.request

    body = urllib.parse.urlencode({
        "client_id": config.OAUTH_CLIENT_ID,
        "client_secret": config.OAUTH_CLIENT_SECRET,
        "code": code,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
    }).encode("utf-8")
    req = urllib.request.Request(
        OAUTH_TOKEN_URL, data=body, method="POST",
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "deldelak-key-recovery"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    tok = data.get("access_token")
    return tok if isinstance(tok, str) and tok else None


def fetch_login(access_token: str) -> Optional[str]:
    """Read GET /user with the one-time token. Nothing about the token is kept."""
    import urllib.request

    req = urllib.request.Request(
        OAUTH_USER_URL,
        headers={"Authorization": f"Bearer {access_token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "deldelak-key-recovery"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    login = data.get("login")
    return login if isinstance(login, str) and login else None


def login_allowed(login: str) -> bool:
    return hmac.compare_digest(login, config.OWNER_LOGIN)
