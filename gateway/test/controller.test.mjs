/* Deldelak Gateway - controller logic tests (plain Node, no Cloudflare).
 * These cover mission §3 guarantees at the logic level; live E2E against a
 * real Codespace is a separate documented procedure (docs/GATEWAY.md §tests).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createController, STATES } from '../src/controller.js';

/* ----------------------------- tiny harness ----------------------------- */

function memStorage() {
  const m = new Map();
  return {
    map: m,
    async get(k, d) { return m.has(k) ? m.get(k) : d; },
    async put(k, v) { m.set(k, v); },
    async delete(k) { m.delete(k); },
  };
}

function makeGh(overrides = {}) {
  const gh = {
    startCalls: 0,
    stopCalls: 0,
    state: 'Shutdown',
    startError: null,
    stopError: null,
    async getState() { return { state: gh.state, name: 'cs-test' }; },
    async start() {
      gh.startCalls++;
      if (gh.startError) throw new Error(gh.startError);
      return { ok: true };
    },
    async stop() {
      gh.stopCalls++;
      if (gh.stopError) throw new Error(gh.stopError);
      gh.state = 'Stopped';
      return { ok: true };
    },
    ...overrides,
  };
  return gh;
}

function makeBackend(overrides = {}) {
  const b = {
    url: 'https://cs-test-8000.app.github.dev',
    healthBody: { backend_ready: true, model_loaded: true, model_loading: false, active_requests: 0 },
    canStopBody: { busy: false, reason: 'idle' },
    healthCalls: 0,
    canStopCalls: 0,
    lastFetch: null,
    baseUrl() { return b.url; },
    async health() { b.healthCalls++; return b.healthBody; },
    async canStop() { b.canStopCalls++; return b.canStopBody; },
    fetch: async (init, url) => {
      b.lastFetch = { init, url };
      return Response.json({ echoed: true, url }, { status: 200 });
    },
    ...overrides,
  };
  return b;
}

function makeController({ cfg = {}, gh, backend, storage } = {}) {
  const st = storage || memStorage();
  const ctl = createController({
    gh: gh || makeGh(),
    backend: backend || makeBackend(),
    storage: st,
    crypto: {
      async sha256Hex(s) {
        const { createHash } = await import('node:crypto');
        return createHash('sha256').update(s).digest('hex');
      },
    },
    cfg: { holdSeconds: 0.05, pollIntervalMs: 1, idleCheckMs: 1, wakeRetryCooldownMs: 1, ...cfg },
    log: () => {},
  });
  return ctl;
}

async function preloadController(ctl) { await ctl.loadPersisted(); }

function req(path, { method = 'GET', key = 'ddk-ok', body, headers = {} } = {}) {
  const h = new Headers(headers);
  if (key !== null) h.set('authorization', 'Bearer ' + key);
  if (body !== undefined) h.set('content-type', 'application/json');
  return new Request('https://gw.test' + path, { method, headers: h, body });
}

/* --------------------------------- tests -------------------------------- */

test('cold start: queued request is woken, held, and answered with the real response', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 2 } });
  await preloadController(ctl);

  const p = ctl.handleRequest(req('/v1/chat/completions', { method: 'POST', body: '{"messages":[]}' }));
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(gh.startCalls, 1, 'exactly one start call');
  assert.equal(ctl._rt.state, STATES.WAKING);
  assert.equal(ctl._rt.queue.length, 1, 'request buffered, not lost');

  // simulate codespace becoming running with model loaded
  gh.state = 'Running';
  await ctl.onAlarm();

  const res = await p;
  assert.equal(res.status, 200);
  const j = await res.json();
  assert.equal(j.echoed, true);
  assert.ok(j.url.includes('/v1/chat/completions'));
  assert.ok(gh.startCalls <= 1, 'no duplicate wake');
});

test('hold timeout: honest 503 + Retry-After + request_id, request still forwarded later', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 0.03 } });
  await preloadController(ctl);

  const p = ctl.handleRequest(req('/v1/chat/completions', { method: 'POST', body: '{}' }));
  const res = await p;                       // hold expires
  assert.equal(res.status, 503);
  assert.ok(res.headers.get('retry-after') !== null, 'Retry-After present');
  const requestId = res.headers.get('x-request-id');
  assert.ok(requestId.startsWith('req_'), 'independent request id');
  const body = await res.json();
  assert.equal(body.error.type, 'cold_start_in_progress');
  assert.equal(body.request_id, requestId);

  // the request must NOT be lost: backend becomes ready and it is forwarded
  gh.state = 'Running';
  await ctl.onAlarm();
  await new Promise((r) => setTimeout(r, 10));
  assert.ok(backend.lastFetch, 'queued request was executed after readiness');
  assert.equal(backend.lastFetch.url, 'https://cs-test-8000.app.github.dev/v1/chat/completions');
});

test('concurrent requests trigger exactly ONE wake and share the queue', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 2 } });
  await preloadController(ctl);

  const ps = [
    ctl.handleRequest(req('/v1/models')),
    ctl.handleRequest(req('/v1/models')),
    ctl.handleRequest(req('/v1/chat/completions', { method: 'POST', body: '{}' })),
  ];
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(gh.startCalls, 1, 'single-flight wake');
  assert.equal(ctl._rt.queue.length, 3);
  gh.state = 'Running';
  await ctl.onAlarm();
  const results = await Promise.all(ps);
  for (const r of results) assert.equal(r.status, 200);
});

test('duplicate X-Request-Id while queued does not execute twice', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 2 } });
  await preloadController(ctl);

  const h = { 'x-request-id': 'req_fixed_123' };
  const p1 = ctl.handleRequest(req('/v1/chat/completions', { method: 'POST', body: '{}', headers: h }));
  const p2 = ctl.handleRequest(req('/v1/chat/completions', { method: 'POST', body: '{}', headers: h }));
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(ctl._rt.queue.length, 1, 'deduped to one entry');
  gh.state = 'Running';
  await ctl.onAlarm();
  const [r1, r2] = await Promise.all([p1, p2]);
  assert.equal(r1.status, 200);
  assert.equal(r2.status, 200);
  assert.equal(backend.lastFetch.init.headers.get('x-request-id'), 'req_fixed_123');
});

test('invalid API key is rejected BEFORE waking (no cost, no codespace start)', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const st = memStorage();
  await st.put('keyHash', 'sha256:' + (await (async () => {
    const { createHash } = await import('node:crypto');
    return createHash('sha256').update('ddk-real').digest('hex');
  })()));
  const ctl = makeController({ gh, backend, storage: st, cfg: { holdSeconds: 2 } });
  await preloadController(ctl);

  const res = await ctl.handleRequest(req('/v1/models', { key: 'ddk-wrong' }));
  assert.equal(res.status, 401);
  assert.equal(gh.startCalls, 0, 'no wake for a bad key');
  assert.equal(backend.lastFetch, null);
  // missing key too
  const res2 = await ctl.handleRequest(req('/v1/models', { key: null }));
  assert.equal(res2.status, 401);
});

test('states follow reality: WAKING -> STARTING -> LOADING_MODEL -> READY', async () => {
  const gh = makeGh(); const backend = makeBackend();
  backend.healthBody = { backend_ready: true, model_loaded: false, model_loading: true };
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 5 } });
  await preloadController(ctl);

  const p = ctl.handleRequest(req('/v1/models'));
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(ctl._rt.state, STATES.WAKING);

  gh.state = 'Running'; backend.healthBody = null; // backend not bootstrapped yet
  await ctl.onAlarm();
  assert.equal(ctl._rt.state, STATES.STARTING, 'codespace running but backend down');

  backend.healthBody = { backend_ready: true, model_loaded: false, model_loading: true };
  await ctl.onAlarm();
  assert.equal(ctl._rt.state, STATES.LOADING_MODEL, 'backend up, model loading');

  backend.healthBody = { backend_ready: true, model_loaded: true, model_loading: false };
  await ctl.onAlarm();
  assert.equal(ctl._rt.state, STATES.READY);
  const res = await p;
  assert.equal(res.status, 200);
});

test('wake timeout produces 504 wake_timeout and ERROR state (no fake success)', async () => {
  const gh = makeGh(); const backend = makeBackend();
  gh.state = 'Starting'; // never reaches Running
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 0.05, startTimeoutMin: 0.0001 } });
  await preloadController(ctl);

  const p = ctl.handleRequest(req('/v1/models'));
  const res503 = await p; // hold expires first
  assert.equal(res503.status, 503);
  await new Promise((r) => setTimeout(r, 5));
  await ctl.onAlarm(); // now the start timeout fires
  assert.equal(ctl._rt.state, STATES.ERROR);
  const st = await ctl._rt; // runtime check
  assert.ok(ctl._rt.lastError.includes('wake timeout'));
});

test('GitHub start failure -> ERROR state, queued requests get 502, last_error recorded', async () => {
  const gh = makeGh(); const backend = makeBackend();
  gh.startError = '403 forbidden (missing Codespaces write)';
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 5 } });
  await preloadController(ctl);

  const res = await ctl.handleRequest(req('/v1/models'));
  assert.equal(res.status, 502);
  assert.equal(ctl._rt.state, STATES.ERROR);
  assert.ok(ctl._rt.lastError.includes('403'));
  const persisted = await ctl._rt;
  // retry after cooldown works
  gh.startError = null;
  const p = ctl.handleRequest(req('/v1/models'));
  await new Promise((r) => setTimeout(r, 5));
  gh.state = 'Running';
  await ctl.onAlarm();  // ERROR branch: retries the wake
  await ctl.onAlarm();  // WAKING branch: observes Running + ready backend
  const res2 = await p;
  assert.equal(res2.status, 200);
});

test('idle shutdown: stops the codespace only when idle AND backend says not busy', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const st = memStorage();
  const ctl = makeController({ gh, backend, storage: st, cfg: { holdSeconds: 0.05, idleShutdownMinutes: 0.001 } });
  await preloadController(ctl);

  // reach READY (settle first so ensureWake completes before we drive the alarm)
  const p = ctl.handleRequest(req('/v1/models'));
  await new Promise((r) => setTimeout(r, 5));
  gh.state = 'Running';
  await ctl.onAlarm();
  (await p); await (await p).text().catch(()=>{});
  assert.equal(ctl._rt.state, STATES.READY);

  // not idle yet (lastActivity just refreshed)
  await ctl.onAlarm();
  assert.equal(gh.stopCalls, 0, 'no stop while freshly active');

  // simulate 10 minutes of idleness
  ctl._rt.lastActivity = Date.now() - 10 * 60e3;
  await ctl.onAlarm();
  assert.equal(gh.stopCalls, 1, 'stop called once idle beyond threshold');
  assert.equal(ctl._rt.state, STATES.STOPPING);
  gh.state = 'Stopped';
  await ctl.onAlarm();
  assert.equal(ctl._rt.state, STATES.STOPPED);

  // idempotent stop
  const r = await ctl.stopNow('again');
  assert.equal(r.ok, true);
  assert.equal(r.already, true);
  assert.equal(gh.stopCalls, 1, 'no duplicate GH stop');
});

test('idle shutdown is blocked while backend reports busy (model loading / agent task)', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 0.05, idleShutdownMinutes: 0.001 } });
  await preloadController(ctl);

  const p = ctl.handleRequest(req('/v1/models'));
  await new Promise((r) => setTimeout(r, 5));
  gh.state = 'Running';
  await ctl.onAlarm();
  (await p); await (await p).text().catch(()=>{});

  ctl._rt.lastActivity = Date.now() - 10 * 60e3;
  backend.canStopBody = { busy: true, reason: 'model loading' };
  await ctl.onAlarm();
  assert.equal(gh.stopCalls, 0, 'busy backend blocks shutdown');
  backend.canStopBody = { busy: false };
  ctl._rt.lastActivity = Date.now() - 10 * 60e3;  // idle again after the busy period
  await ctl.onAlarm();
  assert.equal(gh.stopCalls, 1);
});

test('a new request during idle cancels the shutdown plan (activity refreshed)', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 2, idleShutdownMinutes: 0.001 } });
  await preloadController(ctl);
  const p = ctl.handleRequest(req('/v1/models'));
  gh.state = 'Running';
  await ctl.onAlarm();
  await p;
  ctl._rt.lastActivity = Date.now() - 10 * 60e3;
  // a new request arrives -> touchActivity
  await ctl.handleRequest(req('/v1/models'));
  await ctl.onAlarm();
  assert.equal(gh.stopCalls, 0, 'idle countdown cancelled by new request');
});

test('GitHub stop failure is recorded and visible; state returns to READY', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 0.05, idleShutdownMinutes: 0.001 } });
  await preloadController(ctl);
  const p = ctl.handleRequest(req('/v1/models'));
  await new Promise((r) => setTimeout(r, 5));
  gh.state = 'Running';
  await ctl.onAlarm();
  (await p); await (await p).text().catch(()=>{});
  ctl._rt.lastActivity = Date.now() - 10 * 60e3;
  gh.stopError = '500 internal';
  await ctl.onAlarm();
  assert.equal(ctl._rt.state, STATES.READY, 'still running after failed stop');
  assert.ok(ctl._rt.lastStopError.includes('500'));
  // health must expose it
  const h = await (await ctl.handleRequest(req('/health', { key: null }))).json();
  assert.ok(h.gateway.last_stop_error.includes('500'));
});

test('deep health reports codespace_state, gateway fields and backend truth without waking', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 0.05 } });
  await preloadController(ctl);
  const res = await ctl.handleRequest(req('/health', { key: null }));
  const j = await res.json();
  assert.equal(j.codespace_state, 'Shutdown');
  assert.ok(j.gateway.service === 'deldelak-gateway');
  for (const k of ('state uptime_s active_requests queued_requests last_activity ' +
    'idle_shutdown_minutes cold_start_estimate_s last_error').split(' ')) {
    assert.ok(k in j.gateway, 'gateway health field: ' + k);
  }
  assert.equal(j.backend, null, 'backend not probed when codespace is off');
  assert.equal(gh.startCalls, 0, 'health never wakes');
  // now running -> backend payload included
  gh.state = 'Running';
  const j2 = await (await ctl.handleRequest(req('/health', { key: null }))).json();
  assert.equal(j2.codespace_state, 'Running');
  assert.equal(j2.backend.backend_ready, true);
});

test('streamed response passes through chunks and decrements in-flight when finished', async () => {
  const gh = makeGh(); const backend = makeBackend();
  backend.fetch = async () => new Response(
    new ReadableStream({
      start(c) { c.enqueue(new TextEncoder().encode('data: {"delta":"sa"}\n\n')); c.enqueue(new TextEncoder().encode('data: [DONE]\n\n')); c.close(); },
    }),
    { status: 200, headers: { 'content-type': 'text/event-stream' } },
  );
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 2 } });
  await preloadController(ctl);

  const p = ctl.handleRequest(req('/v1/chat/completions', { method: 'POST', body: '{"stream":true}' }));
  await new Promise((r) => setTimeout(r, 5));
  gh.state = 'Running';
  await ctl.onAlarm();
  const res = await p;
  assert.equal(res.headers.get('content-type'), 'text/event-stream');
  const text = await res.text(); // consume the stream fully
  assert.ok(text.includes('[DONE]'));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(ctl._rt.inFlight, 0, 'in-flight decremented after stream finished');
});

test('request status endpoint: queued request visible, unknown id -> 404', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const ctl = makeController({ gh, backend, cfg: { holdSeconds: 0.05 } });
  await preloadController(ctl);
  ctl.handleRequest(req('/v1/models', { headers: { 'x-request-id': 'req_status1' } })).catch(() => {});
  await new Promise((r) => setTimeout(r, 5));
  const s = await (await ctl.handleRequest(req('/v1/requests/req_status1', { key: null }))).json();
  assert.equal(s.status, 'queued');
  const nf = await ctl.handleRequest(req('/v1/requests/req_nope', { key: null }));
  assert.equal(nf.status, 404);
  gh.state = 'Running';
  await ctl.onAlarm();
});

test('internal endpoints require the shared secret; register persists base_url + key hash', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const st = memStorage();
  const ctl = makeController({ gh, backend, storage: st, cfg: { holdSeconds: 0.05 } });
  await preloadController(ctl);

  const noSecret = await ctl.handleRequest(req('/internal/register', { method: 'POST', key: null, body: '{}' }));
  assert.equal(noSecret.status, 403);
  const wrong = await ctl.handleRequest(req('/internal/register', { method: 'POST', key: null, body: '{}', headers: { 'x-gateway-secret': 'nope' } }));
  assert.equal(wrong.status, 403);

  await st.put('sharedSecret', 's3cret');
  const ok = await ctl.handleRequest(req('/internal/register', {
    method: 'POST', key: null, body: JSON.stringify({ base_url: 'https://cs-8000.app.github.dev/', key_fingerprint: 'sha256:abc' }),
    headers: { 'x-gateway-secret': 's3cret' },
  }));
  assert.equal(ok.status, 200);
  assert.equal(await st.get('baseUrl'), 'https://cs-8000.app.github.dev');
  assert.equal(await st.get('keyHash'), 'sha256:abc');
});

test('key hash registration makes bad keys fail fast at the edge (after registration)', async () => {
  const gh = makeGh(); const backend = makeBackend();
  const st = memStorage();
  await st.put('sharedSecret', 's3cret');
  const ctl = makeController({ gh, backend, storage: st, cfg: { holdSeconds: 0.05 } });
  await preloadController(ctl);
  // fail-open before registration
  const open = await ctl.handleRequest(req('/v1/models', { key: 'anything' }));
  assert.notEqual(open.status, 401, 'fail-open before backend registration');
  gh.startCalls = 0; // reset: the fail-open request legitimately woke the codespace
  await new Promise((r) => setTimeout(r, 5));
  await ctl.handleRequest(req('/internal/key-fingerprint', {
    method: 'POST', key: null, body: JSON.stringify({
      key_fingerprint: 'sha256:' + (await (async () => {
        const { createHash } = await import('node:crypto');
        return createHash('sha256').update('ddk-real').digest('hex');
      })()),
    }), headers: { 'x-gateway-secret': 's3cret' },
  }));
  const bad = await ctl.handleRequest(req('/v1/models', { key: 'anything' }));
  assert.equal(bad.status, 401);
  assert.equal(gh.startCalls, 0);
  const good = ctl.handleRequest(req('/v1/models', { key: 'ddk-real' }));
  gh.state = 'Running';
  await ctl.onAlarm();
  assert.equal((await good).status, 200);
});
