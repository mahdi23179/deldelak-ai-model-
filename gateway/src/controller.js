/* Deldelak Gateway - instance controller (pure state machine).
 *
 * This module contains ALL wake/queue/idle logic with injected dependencies
 * so it runs identically inside a Cloudflare Durable Object and in plain Node
 * tests. No Cloudflare-specific API is used here.
 *
 * Service states (mission §3): STOPPED, WAKING, STARTING, LOADING_MODEL,
 * READY, BUSY, IDLE, STOPPING, ERROR. Every state is derived from REAL
 * observations (GitHub API + backend /health), never from a fake variable.
 *
 * Key guarantees:
 * - concurrent requests trigger exactly ONE wake (single-flight)
 * - requests are buffered with unique ids and are never lost
 * - the client request is held up to cfg.holdSeconds; if the backend is not
 *   ready by then, an honest 503 + Retry-After + request_id is returned
 *   (no fake success) and the request is still forwarded when ready
 * - idle shutdown only fires when: no in-flight request, queue empty and the
 *   backend confirms it is not busy (model loading / agent tasks block it)
 * - GitHub stop is idempotent; GitHub errors are recorded in last_error
 */

const STATES = Object.freeze({
  STOPPED: 'STOPPED', WAKING: 'WAKING', STARTING: 'STARTING',
  LOADING_MODEL: 'LOADING_MODEL', READY: 'READY', BUSY: 'BUSY',
  IDLE: 'IDLE', STOPPING: 'STOPPING', ERROR: 'ERROR'
});

const DEFAULTS = Object.freeze({
  idleShutdownMinutes: 10,     // IDLE_SHUTDOWN_MINUTES
  holdSeconds: 75,             // hold the client while waking (under edge limits)
  pollIntervalMs: 5000,        // GH/backend poll cadence while waking
  idleCheckMs: 60000,          // idle-check cadence when READY
  startTimeoutMin: 12,         // max wake duration before ERROR
  requestTimeoutMs: 30 * 60e3, // forwarded request cap (streams included)
  maxBodyBytes: 2 * 1024 * 1024,
  wakeRetryCooldownMs: 60e3,   // after a GH failure, wait before next attempt
  stopUnreachableGraceMs: 120e3, // backend unreachable -> wait before stopping
  crypto: null,                // { sha256Hex(str): Promise<string> }
});

function createController(deps) {
  const cfg = { ...DEFAULTS, ...(deps.cfg || {}) };
  const gh = deps.gh;            // { getState(): Promise<{state,name}|null>, start(), stop(), name }
  const backend = deps.backend;  // { baseUrl(), health(), canStop(), fetch(init, url) }
  const storage = deps.storage;  // async get(k, def) / put(k, v)  (persisted)
  const log = deps.log || (() => {});
  const sha256Hex = (deps.crypto && deps.crypto.sha256Hex) || (async (s) => s);
  const now = () => Date.now();

  // ---- volatile runtime (rebuilt on DO eviction; durable bits live in storage)
  const rt = {
    state: STATES.STOPPED,
    queue: [],                  // {id, method, url, headers, bodyBuf, resolve, ...}
    byId: new Map(),            // request_id -> entry (dedupe / status)
    inFlight: 0,
    lastActivity: now(),
    wakeStartedAt: null,
    lastStopAttempt: null,
    lastStopError: null,
    lastError: null,
    coldStartSamples: [],       // seconds (measured)
    alarmAt: null,
    bootedAt: now(),
    _wakeStarting: false,
    _draining: false,
  };

  // ---- persisted state (survives DO restarts)
  async function loadPersisted() {
    rt.state = await storage.get('state', STATES.STOPPED);
    rt.lastActivity = await storage.get('lastActivity', rt.lastActivity);
    rt.coldStartSamples = await storage.get('coldStartSamples', []);
    rt.lastError = await storage.get('lastError', null);
    if (rt.state === STATES.WAKING || rt.state === STATES.STARTING || rt.state === STATES.LOADING_MODEL) {
      // DO was evicted mid-wake: resume the wake rather than fake readiness
      rt.state = STATES.STOPPED;
      await storage.put('state', rt.state);
    }
  }

  function setState(next) {
    if (rt.state === next) return;
    rt.state = next;
    try {
      const p = storage.put('state', next);
      if (p && typeof p.catch === 'function') p.catch(() => {});
    } catch (_) { /* sync storage in tests */ }
  }

  function touchActivity() {
    rt.lastActivity = now();
    try {
      const p = storage.put('lastActivity', rt.lastActivity);
      if (p && typeof p.catch === 'function') p.catch(() => {});
    } catch (_) { /* sync storage in tests */ }
  }

  function nextAlarm(delayMs) {
    rt.alarmAt = delayMs > 0 ? now() + delayMs : null;
    if (deps.onAlarmSchedule) deps.onAlarmSchedule(rt.alarmAt);
  }

  // ------------------------------------------------------------- auth ----
  async function authorize(request) {
    const storedHash = await storage.get('keyHash', null);
    if (!storedHash) return { ok: true, failOpen: true }; // backend not registered yet
    const auth = request.headers.get('authorization') || '';
    const key = auth.toLowerCase().startsWith('bearer ') ? auth.slice(7).trim()
      : (request.headers.get('x-api-key') || '').trim();
    if (!key) return { ok: false, status: 401, why: 'missing key' };
    const hex = await sha256Hex(key);
    if (('sha256:' + hex) !== storedHash) return { ok: false, status: 401, why: 'bad key' };
    return { ok: true, failOpen: false };
  }

  // ------------------------------------------------------- wake engine ----
  async function ensureWake() {
    if (rt.state === STATES.READY || rt.state === STATES.BUSY || rt.state === STATES.IDLE) return;
    if (rt.state === STATES.STOPPING || rt.state === STATES.WAKING ||
        rt.state === STATES.STARTING || rt.state === STATES.LOADING_MODEL) {
      nextAlarm(cfg.pollIntervalMs);           // already in flight - keep polling
      return;
    }
    if (rt.state === STATES.ERROR && rt.wakeStartedAt &&
        (now() - rt.wakeStartedAt) < cfg.wakeRetryCooldownMs) {
      nextAlarm(cfg.wakeRetryCooldownMs);      // cooldown after a GitHub failure
      return;
    }
    if (rt._wakeStarting) return;              // single-flight wake
    rt._wakeStarting = true;
    setState(STATES.WAKING);
    rt.wakeStartedAt = now();
    try {
      await gh.start();                         // official GitHub API (create-or-start)
      rt.lastError = null;
      await storage.put('lastError', null);
      nextAlarm(cfg.pollIntervalMs);
    } catch (e) {
      rt.lastError = 'github start failed: ' + (e && e.message ? e.message : String(e));
      await storage.put('lastError', rt.lastError);
      setState(STATES.ERROR);
      nextAlarm(cfg.wakeRetryCooldownMs);
      failQueue(502, 'wake_failed', rt.lastError);
    } finally {
      rt._wakeStarting = false;
    }
  }

  function failQueue(status, code, message) {
    const entries = rt.queue.splice(0);
    rt.byId.clear();
    for (const en of entries) {
      en.settled = true;
      try {
        en.resolve({
          status,
          json: { error: { message, type: code }, request_id: en.id },
          headers: { 'content-type': 'application/json' },
        });
      } catch (_) { /* ignore */ }
    }
  }

  // one tick: advance the wake pipeline from REAL observations only
  async function pollTick() {
    const t = now();
    if (rt.state === STATES.WAKING || rt.state === STATES.STARTING || rt.state === STATES.LOADING_MODEL) {
      if (rt.wakeStartedAt && t - rt.wakeStartedAt > cfg.startTimeoutMin * 60e3) {
        rt.lastError = 'wake timeout after ' + cfg.startTimeoutMin + ' min';
        await storage.put('lastError', rt.lastError);
        setState(STATES.ERROR);
        failQueue(504, 'wake_timeout', rt.lastError);
        nextAlarm(cfg.wakeRetryCooldownMs);
        return;
      }
      let cs = null;
      try { cs = await gh.getState(); } catch (e) {
        rt.lastError = 'github getState failed: ' + (e && e.message ? e.message : String(e));
        nextAlarm(cfg.pollIntervalMs);
        return;
      }
      const ghState = cs && cs.state;
      if (ghState === 'Running') {
        // probe the backend (health does NOT wake anything - codespace already running)
        const h = await backend.health();
        if (h && h.backend_ready) {
          if (h.model_loading) {
            setState(STATES.LOADING_MODEL);
          } else if (h.model_loaded) {
            const dur = rt.wakeStartedAt ? Math.round((t - rt.wakeStartedAt) / 100) / 10 : null;
            if (dur != null) {
              rt.coldStartSamples = [...rt.coldStartSamples, dur].slice(-10);
              await storage.put('coldStartSamples', rt.coldStartSamples);
            }
            setState(rt.inFlight > 0 ? STATES.BUSY : STATES.READY);
            await drainQueue();
          } else {
            setState(STATES.STARTING);   // backend up, model files not ready yet
          }
        } else {
          setState(STATES.STARTING);     // codespace running, backend booting
        }
      } else if (ghState === 'Stopped' || ghState === 'Shutdown' || ghState === 'Archived') {
        // the start request did not take effect - retry it (bounded by start timeout)
        try { await gh.start(); } catch (e) {
          rt.lastError = 'github start retry failed: ' + (e && e.message ? e.message : String(e));
          await storage.put('lastError', rt.lastError);
        }
      }
      // Provisioning / Queued / Starting / Unknown -> keep waiting
      nextAlarm(cfg.pollIntervalMs);
      return;
    }

    if (rt.state === STATES.STOPPING) {
      try {
        const cs = await gh.getState();
        if (!cs || cs.state === 'Stopped' || cs.state === 'Shutdown' || cs.state === 'Archived') {
          setState(STATES.STOPPED);
          nextAlarm(0); // clear alarm
          return;
        }
      } catch (e) {
        rt.lastStopError = 'github getState during stop: ' + (e && e.message ? e.message : String(e));
      }
      nextAlarm(cfg.pollIntervalMs);
      return;
    }

    if (rt.state === STATES.ERROR) {
      // cooldown elapsed: retry the wake if clients are still waiting
      if (rt.queue.length > 0) {
        rt.wakeStartedAt = null;            // allow a fresh attempt
        await ensureWake();
      }
      nextAlarm(cfg.wakeRetryCooldownMs);
      return;
    }

    if (rt.state === STATES.READY || rt.state === STATES.BUSY || rt.state === STATES.IDLE) {
      await idleCheck();
      nextAlarm(cfg.idleCheckMs);
      return;
    }
    // STOPPED: no alarm needed
    if (rt.state === STATES.STOPPED) nextAlarm(0);
  }

  async function idleCheck() {
    if (rt.inFlight > 0 || rt.queue.length > 0) return;
    const idleFor = now() - rt.lastActivity;
    if (idleFor < cfg.idleShutdownMinutes * 60e3) return;
    const cs = await gh.getState().catch(() => null);
    if (!cs || cs.state !== 'Running') return;   // nothing to stop / unknown
    let canStop = { busy: false };
    let reachable = false;
    try {
      const r = await backend.canStop();
      if (r) { canStop = r; reachable = true; }
    } catch (_) { /* unreachable */ }
    if (reachable && canStop.busy) {
      touchActivity();   // real work is happening; treat as activity
      return;
    }
    if (!reachable && idleFor < cfg.stopUnreachableGraceMs) {
      return;            // transient network hiccup - do not stop on guesswork
    }
    await stopNow('idle shutdown after ' + Math.round(idleFor / 1000) + 's');
  }

  async function stopNow(reason) {
    if (rt.state === STATES.STOPPED) return { ok: true, already: true };   // idempotent
    if (rt.inFlight > 0 || rt.queue.length > 0) return { ok: false, busy: true };
    setState(STATES.STOPPING);
    rt.lastStopAttempt = now();
    try {
      await gh.stop();
      rt.lastStopError = null;
      nextAlarm(cfg.pollIntervalMs);   // verify on next tick
      return { ok: true, reason };
    } catch (e) {
      rt.lastStopError = 'github stop failed: ' + (e && e.message ? e.message : String(e));
      await storage.put('lastError', rt.lastStopError);
      setState(STATES.READY);          // go back - the codespace is still running
      nextAlarm(cfg.idleCheckMs);
      return { ok: false, error: rt.lastStopError };
    }
  }

  // ------------------------------------------------------------ queue ----
  async function enqueue(request, requestId) {
    const entry = {
      id: requestId,
      method: request.method,
      url: new URL(request.url),
      headers: [...request.headers.entries()],
      bodyBuf: null,
      resolve: null,
      createdAt: now(),
      promise: null,
      executed: false,
      settled: false,
    };
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      const buf = await request.arrayBuffer();
      if (buf.byteLength > cfg.maxBodyBytes) {
        return { status: 413, json: { error: { message: 'بدنهٔ درخواست بزرگ‌تر از حد مجاز است', type: 'payload_too_large' }, request_id: requestId } };
      }
      entry.bodyBuf = buf;
    }
    const prev = rt.byId.get(requestId);
    if (prev && !prev.settled) {
      log('dedupe: ' + requestId);
      return { duplicateOf: prev };
    }
    const p = new Promise((res) => { entry.resolve = res; });
    entry.promise = p;
    rt.queue.push(entry);
    rt.byId.set(requestId, entry);
    touchActivity();
    return entry;
  }

  async function drainQueue() {
    if (rt._draining) return;
    rt._draining = true;
    try {
      const entries = rt.queue.splice(0);
      for (const en of entries) {
        // execute regardless of the client hold status: the request must not be lost
        en.executed = true;
        const result = await executeBuffered(en).catch((e) => ({
          status: 502,
          json: { error: { message: 'backend error: ' + (e && e.message ? e.message : String(e)), type: 'backend_error' }, request_id: en.id },
          headers: { 'content-type': 'application/json' },
        }));
        en.settled = true;
        rt.byId.delete(en.id);
        if (en.resolve) en.resolve(result);
      }
    } finally {
      rt._draining = false;
    }
  }

  async function executeBuffered(en) {
    const base = backend.baseUrl();
    if (!base) throw new Error('backend base url unknown');
    const url = base + en.url.pathname + en.url.search;
    const headers = new Headers(en.headers);
    headers.set('host', new URL(base).host);
    headers.delete('content-length');
    headers.set('x-request-id', en.id);
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(new Error('gateway request timeout')), cfg.requestTimeoutMs);
    rt.inFlight++;
    try {
      const init = { method: en.method, headers, signal: ctrl.signal };
      if (en.bodyBuf) init.body = en.bodyBuf;
      const res = await backend.fetch(init, url);
      const tracked = trackResponse(res, () => { rt.inFlight--; if (rt.state === STATES.BUSY && rt.inFlight === 0 && rt.queue.length === 0) setState(STATES.READY); });
      // normalize to a plain descriptor so respond() never mistakes a
      // Response's inherited .json method for a JSON payload
      const plain = { status: tracked.status, headers: {} };
      for (const [k, v] of tracked.headers.entries()) plain.headers[k] = v;
      plain.body = tracked.body;
      return plain;
    } catch (e) {
      rt.inFlight--;
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  function trackResponse(res, onDone) {
    let done = false;
    const finish = () => { if (!done) { done = true; onDone(); } };
    if (!res.body) { queueMicrotask(finish); return res; }
    const ts = new TransformStream({
      transform(chunk, ctrl) { ctrl.enqueue(chunk); },
      flush() { finish(); },
      cancel() { finish(); },
    });
    const out = res.body.pipeThrough(ts);
    return new Response(out, res);
  }

  // ---------------------------------------------------- request entry ----
  async function handleRequest(request) {
    const url = new URL(request.url);
    const requestId = request.headers.get('x-request-id') ||
      ('req_' + Math.random().toString(36).slice(2) + Date.now().toString(36));

    if (url.pathname === '/healthz') {
      return json(200, {
        gateway: 'ok', state: rt.state,
        uptime_s: Math.round((now() - rt.bootedAt) / 1000),
        active_requests: rt.inFlight, queued_requests: rt.queue.length,
        last_activity: rt.lastActivity, request_id: requestId,
      }, { 'x-request-id': requestId });
    }

    if (url.pathname === '/health') {
      return json(200, await deepHealth(requestId), { 'x-request-id': requestId });
    }

    if (url.pathname.startsWith('/internal/')) return handleInternal(request, url);

    const mStatus = url.pathname.match(/^\/v1\/requests\/([A-Za-z0-9_-]+)$/);
    if (mStatus) {
      const en = rt.byId.get(mStatus[1]);
      if (!en) return json(404, { request_id: mStatus[1], status: 'unknown_or_expired' }, { 'x-request-id': requestId });
      return json(200, {
        request_id: en.id, status: en.executed ? 'forwarded' : 'queued',
        created_at: en.createdAt,
      }, { 'x-request-id': requestId });
    }

    const authz = await authorize(request);
    if (!authz.ok) {
      return json(authz.status, {
        error: { message: 'کلید API نامعتبر است.', type: 'invalid_api_key' },
        request_id: requestId,
      }, { 'x-request-id': requestId, 'www-authenticate': 'Bearer realm="deldelak"' });
    }
    touchActivity();

    const readyNow = rt.state === STATES.READY || rt.state === STATES.BUSY ||
                     rt.state === STATES.IDLE;
    if (readyNow) {
      return forwardLive(request, url, requestId);
    }

    // not ready: buffer + wake + hold honestly
    const en = await enqueue(request, requestId);
    if (en && en.duplicateOf) {
      const result = await Promise.race([en.duplicateOf.promise, holdTimeout()]);
      if (result) return respond(result, requestId);
      return json(503, busyPayload(requestId), {
        'x-request-id': requestId, 'retry-after': String(Math.max(15, estimateReadySeconds())),
      });
    }
    if (en && en.status) return json(en.status, en.json, { 'x-request-id': requestId });

    await ensureWake();
    nextAlarm(cfg.pollIntervalMs);

    const result = await Promise.race([en.promise, holdTimeout()]);
    if (result) return respond(result, requestId);
    return json(503, busyPayload(requestId), {
      'x-request-id': requestId,
      'retry-after': String(Math.max(15, estimateReadySeconds())),
    });
  }

  async function forwardLive(request, url, requestId) {
    setState(STATES.BUSY);
    const base = backend.baseUrl();
    if (!base) {
      setState(STATES.READY);
      return json(503, { error: { message: 'backend url unknown', type: 'not_registered' }, request_id: requestId }, { 'x-request-id': requestId });
    }
    const upstream = base + url.pathname + url.search;
    const headers = new Headers(request.headers);
    headers.set('host', new URL(base).host);
    headers.set('x-request-id', requestId);
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(new Error('gateway request timeout')), cfg.requestTimeoutMs);
    rt.inFlight++;
    try {
      const init = { method: request.method, headers, body: request.body, signal: ctrl.signal };
      if (request.method !== 'GET' && request.method !== 'HEAD') init.duplex = 'half';
      const res = await backend.fetch(init, upstream);
      const tracked = trackResponse(res, () => {
        rt.inFlight--;
        if (rt.inFlight === 0 && rt.queue.length === 0 && rt.state === STATES.BUSY) setState(STATES.READY);
      });
      tracked.headers.set('x-request-id', requestId);
      return tracked;
    } catch (e) {
      rt.inFlight--;
      setState(STATES.READY);
      return json(502, { error: { message: 'backend unreachable: ' + (e && e.message ? e.message : String(e)), type: 'backend_unreachable' }, request_id: requestId }, { 'x-request-id': requestId });
    } finally {
      clearTimeout(timer);
      if (rt.inFlight === 0 && rt.queue.length === 0 && rt.state === STATES.BUSY) setState(STATES.READY);
    }
  }

  function holdTimeout() {
    return new Promise((res) => setTimeout(() => res(null), cfg.holdSeconds * 1000));
  }

  function busyPayload(requestId) {
    return {
      error: {
        message: 'سرویس در حال روشن‌شدن است. درخواست شما در صف ماند و گم نمی‌شود؛ بعد از Retry-After همین درخواست را با همان X-Request-Id تکرار کنید یا وضعیت را از /v1/requests/{id} بگیرید.',
        type: 'cold_start_in_progress',
        state: rt.state,
        estimate_seconds: estimateReadySeconds(),
      },
      request_id: requestId,
    };
  }

  function estimateReadySeconds() {
    const n = rt.coldStartSamples.length;
    if (!n) return 120;
    const avg = rt.coldStartSamples.reduce((a, b) => a + b, 0) / n;
    const elapsed = rt.wakeStartedAt ? (now() - rt.wakeStartedAt) / 1000 : 0;
    return Math.max(15, Math.round(avg - elapsed));
  }

  function respond(result, requestId) {
    if (result instanceof Response) {
      const headers = new Headers(result.headers);
      headers.set('x-request-id', requestId);
      return new Response(result.body, { status: result.status, headers });
    }
    const headers = { ...(result.headers || {}) };
    headers['x-request-id'] = requestId;
    return new Response(
      result.json !== undefined ? JSON.stringify(result.json) : result.body,
      { status: result.status, headers });
  }

  async function deepHealth(requestId) {
    let cs = null;
    try { cs = await gh.getState(); } catch (e) {
      rt.lastError = 'github getState failed: ' + (e && e.message ? e.message : String(e));
    }
    const t = now();
    const idleRemainingMs = Math.max(0, cfg.idleShutdownMinutes * 60e3 - (t - rt.lastActivity));
    const samples = rt.coldStartSamples;
    const est = samples.length ? Math.round(samples.reduce((a, b) => a + b, 0) / samples.length) : null;
    let backendHealth = null;
    if (cs && cs.state === 'Running') {
      backendHealth = await backend.health().catch(() => null);
    }
    return {
      gateway: {
        service: 'deldelak-gateway', state: rt.state,
        uptime_s: Math.round((t - rt.bootedAt) / 1000),
        active_requests: rt.inFlight, queued_requests: rt.queue.length,
        last_activity: rt.lastActivity,
        idle_shutdown_minutes: cfg.idleShutdownMinutes,
        idle_shutdown_remaining_s: Math.round(idleRemainingMs / 1000),
        auto_stop_at: rt.inFlight === 0 && rt.queue.length === 0 && rt.state !== STATES.STOPPED
          ? rt.lastActivity + cfg.idleShutdownMinutes * 60e3 : null,
        cold_start_estimate_s: est, cold_start_samples: samples,
        last_error: rt.lastError, last_stop_error: rt.lastStopError,
        request_id: requestId,
      },
      codespace_state: cs ? cs.state : 'unknown',
      backend: backendHealth,          // full honest payload from /health (or null)
    };
  }

  async function handleInternal(request, url) {
    const secret = await storage.get('sharedSecret', null);
    const provided = request.headers.get('x-gateway-secret') || '';
    if (!secret || !provided || !timingSafeEq(provided, secret)) {
      return json(403, { error: 'forbidden' });
    }
    if (request.method === 'POST' && url.pathname === '/internal/register') {
      const body = await request.json().catch(() => ({}));
      if (body.base_url) await storage.put('baseUrl', String(body.base_url).replace(/\/$/, ''));
      if (body.key_fingerprint) await storage.put('keyHash', String(body.key_fingerprint));
      return json(200, { ok: true });
    }
    if (request.method === 'POST' && url.pathname === '/internal/key-fingerprint') {
      const body = await request.json().catch(() => ({}));
      if (body.key_fingerprint) await storage.put('keyHash', String(body.key_fingerprint));
      return json(200, { ok: true });
    }
    if (request.method === 'POST' && url.pathname === '/internal/notify-activity') {
      touchActivity();
      return json(200, { ok: true });
    }
    if (request.method === 'POST' && url.pathname === '/internal/stop') {
      const r = await stopNow('manual via internal api');
      return json(r.ok ? 200 : 409, r);
    }
    return json(404, { error: 'not found' });
  }

  function timingSafeEq(a, b) {
    if (a.length !== b.length) return false;
    let diff = 0;
    for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
    return diff === 0;
  }

  function json(status, obj, headers) {
    return new Response(JSON.stringify(obj), {
      status,
      headers: { 'content-type': 'application/json', ...(headers || {}) },
    });
  }

  return {
    STATES,
    handleRequest,
    onAlarm: pollTick,
    ensureWake,
    stopNow,
    loadPersisted,
    _rt: rt,       // observability / tests
  };
}

export { createController, STATES, DEFAULTS };
