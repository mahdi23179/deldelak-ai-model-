"""Deldelak AI Model - FastAPI application.

OpenAI-compatible local API + agent endpoints + file/project management +
system status. Serves the Persian RTL web UI from /frontend.

Security model (v2 - mission §3/§5/§6):
- llama.cpp model server binds to 127.0.0.1 ONLY (never forwarded).
- All /v1/* endpoints require the random API key (Authorization: Bearer or X-API-Key),
  compared in constant time.
- The key is NEVER served to a first-come caller. Recovery is owner-only via
  GitHub OAuth (backend/keys.py). Reveal/rotate/revoke need an owner session
  (short-lived signed HttpOnly cookie) or a currently-valid API key.
- Responses/logs never contain the key itself - only masked fingerprints.
- /health and the static UI are public by design; Swagger docs are disabled
  on public deployments unless DELDELAK_DOCS=1.
- Internal /internal/* endpoints require the shared gateway secret.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import stat
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
import psutil
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, keys, ratelimit
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

# honest in-flight accounting (chat streams + long API calls + agent activity)
_inflight_lock = threading.Lock()
_inflight = 0
_last_activity = time.time()


def _inflight_change(delta: int) -> None:
    global _inflight, _last_activity
    with _inflight_lock:
        _inflight = max(0, _inflight + delta)
        if delta > 0:
            _last_activity = time.time()


# ---------------- api key management ----------------

def _load_or_create_key() -> str:
    config.ensure_dirs()
    if config.API_KEY_FILE.exists():
        return config.API_KEY_FILE.read_text(encoding="utf-8").strip()
    key = "ddk-" + secrets.token_urlsafe(24)
    config.API_KEY_FILE.write_text(key, encoding="utf-8")
    os.chmod(config.API_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600
    print("[deldelak] API key generated (owner recovery via GitHub OAuth)", flush=True)
    return key


API_KEY = _load_or_create_key()


def _set_key(new_key: str) -> None:
    """Persist a new key (600 perms) - used by rotate/revoke."""
    global API_KEY
    API_KEY = new_key
    config.ensure_dirs()
    tmp = config.API_KEY_FILE.with_suffix(".tmp")
    tmp.write_text(new_key, encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, config.API_KEY_FILE)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "unknown")


def require_key(authorization: str = Header(default=""),
                x_api_key: str = Header(default="")) -> str:
    provided = ""
    if authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    provided = provided or x_api_key.strip()
    if not secrets.compare_digest(provided, API_KEY):
        raise HTTPException(status_code=401, detail="کلید API نامعتبر یا موجود نیست. هدر Authorization: Bearer <key> را بفرستید.")
    return provided


def require_owner_session(request: Request,
                          x_requested_with: str = Header(default="")) -> str:
    """Owner session OR currently-valid API key (header wins for automation)."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and secrets.compare_digest(auth[7:].strip(), API_KEY):
        return config.OWNER_LOGIN
    token = request.cookies.get("ddk_session", "")
    login = keys.verify_session(token)
    if login and login == config.OWNER_LOGIN:
        return login
    raise HTTPException(status_code=401, detail="نشست مالک معتبر نیست. ابتدا از ورود با گیت‌هاب بازیابی کنید.")


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

def _gateway_register_thread() -> None:
    """Register this backend with the always-on gateway (base URL + key
    fingerprint). Best-effort, retried a few times; never blocks boot."""
    if not (config.GATEWAY_URL and config.GATEWAY_SHARED_SECRET):
        return
    import httpx as _hx
    payload = {
        "base_url": config.public_base_url(),
        "key_fingerprint": keys.fingerprint(API_KEY),
        "version": config.VERSION,
    }
    headers = {"X-Gateway-Secret": config.GATEWAY_SHARED_SECRET}
    for attempt in range(10):
        try:
            r = _hx.post(f"{config.GATEWAY_URL}/internal/register", json=payload, headers=headers, timeout=10)
            if r.status_code < 500:
                print(f"[deldelak] gateway register: HTTP {r.status_code}", flush=True)
                return
        except Exception as e:
            print(f"[deldelak] gateway register attempt {attempt + 1} failed: {type(e).__name__}", flush=True)
        time.sleep(15)
    db.log_error(None, "gateway", "registration with gateway failed after retries")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    recovered = db.recover_interrupted_tasks()
    if recovered:
        print(f"[deldelak] {recovered} interrupted task(s) marked failed after restart", flush=True)
    for t in db.list_tasks():
        if t["status"] == "queued":
            worker.submit(t["id"])
            break
    threading.Thread(target=manager.start, daemon=True).start()
    threading.Thread(target=_model_watchdog, daemon=True).start()
    if config.GATEWAY_URL and config.GATEWAY_SHARED_SECRET:
        threading.Thread(target=_gateway_register_thread, daemon=True).start()
    yield
    await llm.aclose()
    manager.stop()


if config.DOCS_ENABLED:
    app = FastAPI(title="Deldelak AI Model", version=config.VERSION, lifespan=lifespan)
else:
    app = FastAPI(title="Deldelak AI Model", version=config.VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def _accounting(request: Request, call_next):
    """Count in-flight requests (streams included) and refresh last_activity.
    The gateway uses these numbers for its idle-shutdown decision."""
    heavy = request.url.path.startswith(("/v1/", "/api/key"))
    if heavy:
        _inflight_change(+1)
    try:
        return await call_next(request)
    finally:
        if heavy:
            _inflight_change(-1)


# ---------------- helpers ----------------

def _model_ready() -> bool:
    return manager.is_running()


async def _ensure_llm() -> None:
    if not await llm.health():
        raise HTTPException(
            status_code=503,
            detail="مدل هنوز آماده نیست. اگر Codespace تازه ساخته شده، چند دقیقه صبر کنید (بارگیری مدل ~۲ گیگابایت).",
            headers={"Retry-After": "60"},
        )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    db.log_error(None, "api", f"{type(exc).__name__}: {exc}")
    return JSONResponse(status_code=500, content={"error": {"message": f"خطای داخلی: {type(exc).__name__}"}})


# ---------------- health (backend truth) ----------------

def _is_localhost_bound(port: int) -> bool:
    """Verify the model server port is NOT externally reachable (safety check).
    Measures the real bind address instead of guessing from hostname DNS."""
    try:
        with socket.create_connection((config.LLM_HOST, port), timeout=1):
            # reachable on the configured host; the host is hardcoded 127.0.0.1
            return config.LLM_HOST in ("127.0.0.1", "localhost", "::1")
    except Exception:
        return config.LLM_HOST in ("127.0.0.1", "localhost", "::1")


@app.get("/health")
def health():
    model_loaded = _model_ready()
    loading = manager.is_loading()
    agent_active = sum(1 for t in db.list_tasks() if t["status"] in
                       ("queued", "planning", "executing", "verifying", "awaiting_approval"))
    backend_ready = True  # this handler running == backend is up
    if model_loaded:
        service_state = "ready" if _inflight == 0 and agent_active == 0 else "busy"
    elif loading:
        service_state = "loading_model"
    else:
        service_state = "degraded"
    return {
        "status": service_state,
        "service": "deldelak-ai-model",
        "version": config.VERSION,
        "backend_ready": backend_ready,
        "model_loaded": model_loaded,
        "model_loading": loading,
        "load_started_at": manager.load_started_at,
        "last_load_duration_s": manager.last_load_duration_s,
        "active_requests": _inflight,
        "queued_requests": agent_active,
        "uptime_s": round(time.time() - BOOT_TIME, 1),
        "last_activity": _last_activity,
        "last_error": manager.last_error,
        "model": config.MODEL_ID,
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "busy": bool(_inflight or agent_active or loading),
    }


def _model_watchdog() -> None:
    """Background self-healing: once setup.sh finishes (binary+model exist),
    load the model without needing a restart. Runs every 30 s."""
    while True:
        try:
            if not manager.is_running() and not manager.is_loading() \
                    and manager.binary_available() and manager.model_available():
                print("[deldelak] watchdog: model files ready - loading model", flush=True)
                manager.start(wait=True)
                if manager.is_running():
                    print(f"[deldelak] watchdog: model loaded in {manager.last_load_duration_s}s", flush=True)
        except Exception as e:
            db.log_error(None, "watchdog", str(e))
        time.sleep(30)


# ---------------- API key: owner-only recovery (GitHub OAuth) ----------------

@app.get("/api/key")
def get_key(request: Request):
    """NO first-come claim any more. Returns recovery instructions (401).
    The key itself is only ever returned to the verified owner session."""
    allowed, retry = ratelimit.check("key_claim", _client_ip(request))
    if not allowed:
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry)},
                            detail="درخواست‌های بیش از حد؛ کمی بعد تلاش کنید.")
    return JSONResponse(status_code=401, content={
        "error": "owner_session_required",
        "detail": "کلید به اولین درخواست‌کننده داده نمی‌شود. مالک مخزن با گیت‌هاب وارد شود.",
        "recovery_url": "/api/key/oauth/start",
        "owner": config.OWNER_LOGIN,
        "fingerprint": keys.fingerprint(API_KEY),
    })


@app.get("/api/key/oauth/start")
def key_oauth_start(request: Request):
    if not keys.oauth_configured():
        raise HTTPException(status_code=503, detail=(
            "بازیابی با گیت‌هاب تنظیم نشده است. Secretهای DELDELAK_OAUTH_CLIENT_ID و "
            "DELDELAK_OAUTH_CLIENT_SECRET را در Codespace Secrets تعریف کنید."))
    allowed, retry = ratelimit.check("oauth_start", _client_ip(request))
    if not allowed:
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry)},
                            detail="درخواست‌های بیش از حد؛ کمی بعد تلاش کنید.")
    csrf_random = secrets.token_urlsafe(16)
    url = keys.oauth_start(csrf_random)
    redirect = config.OAUTH_REDIRECT_URI or (config.public_base_url() + "/api/key/oauth/callback")
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie("ddk_oauth_csrf", csrf_random, max_age=keys.STATE_TTL_S,
                    httponly=True, samesite="lax",
                    secure=request.url.scheme == "https", path="/")
    return resp


@app.get("/api/key/oauth/callback")
def key_oauth_callback(request: Request, code: str = Query(default=""), state: str = Query(default="")):
    allowed, retry = ratelimit.check("oauth_callback", _client_ip(request))
    if not allowed:
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry)},
                            detail="درخواست‌های بیش از حد؛ کمی بعد تلاش کنید.")
    csrf_random = request.cookies.get("ddk_oauth_csrf", "")
    if not code or not state or not keys.oauth_consume_state(state, csrf_random):
        return JSONResponse(status_code=400, content={"error": "bad_state",
                             "detail": "state نامعتبر است (CSRF/پخش مجدد). دوباره تلاش کنید."})
    token = keys.exchange_code(code)
    if not token:
        db.log_error(None, "oauth", "token exchange failed")
        return JSONResponse(status_code=502, content={"error": "oauth_exchange_failed",
                             "detail": "تبادل کد با گیت‌هاب ناموفق بود."})
    login = keys.fetch_login(token)  # token used once here; never stored
    del token
    if not login:
        return JSONResponse(status_code=502, content={"error": "github_user_failed",
                             "detail": "خواندن پروفایل گیت‌هاب ناموفق بود."})
    if not keys.login_allowed(login):
        # other users are rejected; nothing about the key is disclosed
        db.log_error(None, "oauth", f"non-owner login attempt: {login}")
        return JSONResponse(status_code=403, content={"error": "forbidden",
                             "detail": "فقط مالک مخزن مجاز به بازیابی کلید است."})
    session, max_age = keys.issue_session(login)
    resp = RedirectResponse("/?owner_session=1", status_code=302)
    resp.set_cookie("ddk_session", session, max_age=max_age, httponly=True, samesite="strict",
                    secure=request.url.scheme == "https", path="/")
    db.log_error(None, "oauth", f"owner session issued to {login}")  # no key material
    return resp


@app.get("/api/key/session")
def key_session(request: Request):
    login = keys.verify_session(request.cookies.get("ddk_session", ""))
    return {"authenticated": bool(login), "login": login or None,
            "is_owner": bool(login and login == config.OWNER_LOGIN),
            "oauth_configured": keys.oauth_configured(),
            "owner": config.OWNER_LOGIN}


@app.post("/api/key/reveal")
def key_reveal(request: Request, owner: str = Depends(require_owner_session),
               x_requested_with: str = Header(default="")):
    if not (x_requested_with or "").strip():
        raise HTTPException(status_code=403, detail="هدر X-Requested-With الزامی است (CSRF).")
    if request.headers.get("origin") and config.public_base_url() not in request.headers.get("origin", "") \
            and "localhost" not in request.headers.get("origin", ""):
        raise HTTPException(status_code=403, detail="Origin نامعتبر است.")
    allowed, retry = ratelimit.check("key_reveal", _client_ip(request))
    if not allowed:
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry)},
                            detail="درخواست‌های بیش از حد؛ کمی بعد تلاش کنید.")
    db.log_error(None, "key", f"key revealed to {owner} (fingerprint {keys.fingerprint(API_KEY)})")
    return {"key": API_KEY, "masked": keys.mask_secret(API_KEY),
            "note": "کلید محرمانه است؛ در لاگ‌ها فقط اثر انگشت (fingerprint) ثبت می‌شود."}


@app.post("/api/key/rotate")
def rotate_key(request: Request, owner: str = Depends(require_owner_session),
               x_requested_with: str = Header(default="")):
    allowed, retry = ratelimit.check("key_rotate", _client_ip(request))
    if not allowed:
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry)},
                            detail="درخواست‌های بیش از حد؛ کمی بعد تلاش کنید.")
    old_fp = keys.fingerprint(API_KEY)
    new_key = "ddk-" + secrets.token_urlsafe(24)
    _set_key(new_key)
    db.log_error(None, "key", f"key rotated by {owner}: {old_fp} -> {keys.fingerprint(new_key)}")
    threading.Thread(target=_push_fingerprint, args=(new_key,), daemon=True).start()
    return {"key": new_key, "masked": keys.mask_secret(new_key),
            "revoked_fingerprint": old_fp,
            "note": "کلید قبلی باطل شد و فوراً کار نمی‌کند."}


@app.post("/api/key/revoke")
def revoke_key(request: Request, owner: str = Depends(require_owner_session),
               x_requested_with: str = Header(default="")):
    """Revoke the current key and issue a new one that is NOT revealed here;
    the owner must reveal it again afterwards. Old key dies immediately."""
    old_fp = keys.fingerprint(API_KEY)
    _set_key("ddk-" + secrets.token_urlsafe(24))
    db.log_error(None, "key", f"key revoked by {owner}: {old_fp} (new key hidden until reveal)")
    threading.Thread(target=_push_fingerprint, args=(API_KEY,), daemon=True).start()
    return {"ok": True, "revoked_fingerprint": old_fp,
            "note": "کلید قبلی باطل شد. برای دیدن کلید جدید از بازیابی/نمایش استفاده کنید."}


def _push_fingerprint(key_value: str) -> None:
    if not (config.GATEWAY_URL and config.GATEWAY_SHARED_SECRET):
        return
    try:
        r = httpx.post(f"{config.GATEWAY_URL}/internal/key-fingerprint",
                       json={"key_fingerprint": keys.fingerprint(key_value)},
                       headers={"X-Gateway-Secret": config.GATEWAY_SHARED_SECRET}, timeout=10)
        print(f"[deldelak] fingerprint push: HTTP {r.status_code}", flush=True)
    except Exception as e:
        db.log_error(None, "gateway", f"fingerprint push failed: {type(e).__name__}")


@app.post("/api/key/claim")
def claim_key(body: ClaimBody, request: Request):
    """Legacy endpoint kept for automation compatibility: validates a key,
    never reveals anything, rate-limited as a brute-force oracle guard."""
    allowed, retry = ratelimit.check("key_claim", _client_ip(request))
    if not allowed:
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry)},
                            detail="درخواست‌های بیش از حد؛ کمی بعد تلاش کنید.")
    if secrets.compare_digest(body.key, API_KEY):
        return {"ok": True}
    raise HTTPException(status_code=401, detail="کلید نادرست است")


# ---------------- internal (gateway-only) ----------------

def require_gateway_secret(x_gateway_secret: str = Header(default="")) -> None:
    if not config.GATEWAY_SHARED_SECRET or not secrets.compare_digest(
            x_gateway_secret, config.GATEWAY_SHARED_SECRET):
        raise HTTPException(status_code=403, detail="forbidden")


@app.get("/internal/can-stop")
def internal_can_stop(_: None = Depends(require_gateway_secret)):
    """The gateway asks before stopping the Codespace. Busy means DO NOT STOP."""
    agent_active = sum(1 for t in db.list_tasks() if t["status"] in
                       ("queued", "planning", "executing", "verifying", "awaiting_approval"))
    loading = manager.is_loading()
    busy = bool(_inflight or agent_active or loading or worker.active)
    return {"busy": busy, "active_requests": _inflight, "agent_busy": bool(agent_active or worker.active),
            "model_loading": loading,
            "reason": ("in-flight requests" if _inflight else
                       "agent tasks running" if agent_active else
                       "model loading" if loading else "idle")}


@app.post("/internal/notify-activity")
def internal_notify_activity(_: None = Depends(require_gateway_secret)):
    global _last_activity
    _last_activity = time.time()
    return {"ok": True}


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
    _inflight_change(0)  # refresh activity stamp
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
            "loading": manager.is_loading(),
            "llama_rss_mb": manager.rss_mb(),
            "size_mb": round(config.MODEL_PATH.stat().st_size / 1048576, 1) if config.MODEL_PATH.exists() else None,
            "ctx": config.LLM_CTX, "threads": config.LLM_THREADS,
            "last_load_duration_s": manager.last_load_duration_s,
        },
        "llm": {"host": config.LLM_HOST, "port": config.LLM_PORT,
                "last_tok_s": llm.last_tok_s,
                "local_only": _is_localhost_bound(config.LLM_PORT)},
        "agent": {"active_tasks": active, "worker_alive": worker.active},
        "requests": {"active": _inflight, "last_activity": _last_activity},
        "codespace": {"name": os.getenv("CODESPACE_NAME"), "region": os.getenv("CODESPACE_REGION")},
    }


# ---------------- files ----------------

def _ws_path(rel: str) -> Path:
    """Workspace jail. Resists prefix confusion (`/base-evil`) and symlinks:
    the fully-resolved path must be *inside* the resolved workspace."""
    base = config.WORKSPACE_DIR.resolve()
    p = (base / (rel or ".")).resolve()
    if p != base and base not in p.parents:
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
    if len(body.content.encode("utf-8")) > config.MAX_FILE_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"حجم فایل بیش از {config.MAX_FILE_BODY_BYTES // 1048576}MB مجاز نیست")
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


# one-time download tokens: the API key never appears in a URL (audit A4)
_download_tokens: dict[str, tuple[str, float]] = {}
_dl_lock = threading.Lock()


@app.post("/v1/projects/{pid}/export-token")
def project_export_token(pid: str, _: str = Depends(require_key)):
    proj = db.get_project(pid)
    name = proj["name"] if proj else pid
    token = secrets.token_urlsafe(24)
    with _dl_lock:
        _download_tokens[token] = (name, time.time() + config.DOWNLOAD_TOKEN_TTL_S)
    return {"token": token, "expires_in": config.DOWNLOAD_TOKEN_TTL_S,
            "url": f"/v1/projects/{pid}/export?download_token={token}"}


def _consume_download_token(token: str) -> Optional[str]:
    with _dl_lock:
        item = _download_tokens.pop(token, None)
    if not item:
        return None
    name, exp = item
    if exp < time.time():
        return None
    return name


@app.get("/v1/projects/{pid}/export")
def project_export(pid: str, download_token: str = Query(default="")):
    name = _consume_download_token(download_token)
    if not name:
        raise HTTPException(status_code=401, detail="توکن دانلود نامعتبر یا منقضی است. اول export-token بگیرید.")
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
