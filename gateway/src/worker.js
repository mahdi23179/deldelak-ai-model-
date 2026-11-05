/* Deldelak Gateway - Cloudflare Worker + Durable Object entry point.
 *
 * Deployment (see docs/GATEWAY.md):
 *   wrangler secret put GH_TOKEN           # fine-grained PAT, Codespaces: write on this repo
 *   wrangler secret put GATEWAY_SHARED_SECRET
 *   wrangler deploy
 * Optional vars (wrangler.toml [vars]): OWNER, REPO, CODESPACE_NAME,
 *   CODESPACE_REF, IDLE_SHUTDOWN_MINUTES, HOLD_SECONDS, START_TIMEOUT_MIN.
 *
 * Security notes (mission §3):
 * - the GitHub token lives ONLY in a Cloudflare secret; it is never returned
 *   to clients and never logged
 * - Codespaces start/stop use the official REST API only (no issues, no
 *   keep-alive pings, no unofficial endpoints)
 * - the worker is fully independent of the Codespace: it always accepts
 *   requests, wakes the Codespace when needed and buffers the client request
 */

import { createController, STATES } from './controller.js';

const SHA = {
  async sha256Hex(str) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
    return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
  },
};

/* ---------------------------------------------------------------- GitHub */

class GitHubCodespaces {
  constructor(env) {
    this.env = env;
    this.owner = env.OWNER || 'mahdi23179';
    this.repo = env.REPO || 'deldelak-ai-model-';
    this.codespaceName = env.CODESPACE_NAME || '';
    this.base = 'https://api.github.com';
  }

  headers() {
    const token = this.env.GH_TOKEN;
    if (!token) throw new Error('GH_TOKEN secret is not configured');
    return {
      'authorization': `Bearer ${token}`,
      'accept': 'application/vnd.github+json',
      'user-agent': 'deldelak-gateway',
      'x-github-api-version': '2022-11-28',
    };
  }

  async api(path, init = {}) {
    const res = await fetch(this.base + path, {
      ...init,
      headers: { ...this.headers(), ...(init.headers || {}) },
    });
    if (res.status === 404) return null;
    if (res.status === 409 || res.status === 412) {
      // 409: codespace already starting/stopping - treat as transient success
      return { ok: true, conflict: true };
    }
    if (!res.ok) {
      let detail = '';
      try { detail = (await res.text()).slice(0, 300); } catch (_) { /* */ }
      const e = new Error(`github ${res.status} on ${path}: ${detail}`);
      e.status = res.status;
      throw e;
    }
    return res.json();
  }

  async listCodespaces() {
    const data = await this.api(`/repos/${this.owner}/${this.repo}/codespaces?per_page=100`);
    return (data && data.codespaces) || [];
  }

  async getOrCreate() {
    if (this.codespaceName) {
      const list = await this.listCodespaces();
      const found = list.find((c) => c.name === this.codespaceName);
      if (found) return found;
      // explicit name configured but missing -> fall through to create
    }
    const list = await this.listCodespaces();
    if (list.length > 0) return list[0];
    // none exists: create one with the first available machine (honest A21 fix)
    const machines = await this.api(`/repos/${this.owner}/${this.repo}/codespaces/machines?ref=${this.env.CODESPACE_REF || 'main'}`);
    const machine = machines && machines.machines && machines.machines[0];
    const body = { ref: this.env.CODESPACE_REF || 'main' };
    if (machine) body.machine = machine.name;
    const created = await this.api(`/repos/${this.owner}/${this.repo}/codespaces`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    return created;
  }

  async getState() {
    const cs = await this.getOrCreate();
    if (!cs) return null;
    this.codespaceName = cs.name;
    return { state: cs.state, name: cs.name };
  }

  async start() {
    const cs = await this.getOrCreate();
    if (!cs) throw new Error('no codespace available and creation failed');
    this.codespaceName = cs.name;
    if (cs.state === 'Running') return { ok: true, already: true };
    if (cs.state === 'Provisioning' || cs.state === 'Queued' || cs.state === 'Starting') {
      return { ok: true, already: true };
    }
    // official start endpoint for the authenticated user's codespace
    return this.api(`/user/codespaces/${cs.name}/start`, { method: 'POST' });
  }

  async stop() {
    const name = this.codespaceName || (await this.getOrCreate()).name;
    // idempotent by contract: stopping an already-stopped codespace is a no-op
    return this.api(`/user/codespaces/${name}/stop`, { method: 'POST' });
  }
}

/* ------------------------------------------------------- durable object */

export class InstanceDO {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this._loaded = false;
    this._baseCache = null;

    const self = this;
    const lazyBackend = {
      baseUrl() { return self._baseCache || null; },
      health() {
        const base = self._baseCache;
        if (!base) return Promise.resolve(null);
        return fetch(base + '/health', { headers: { 'user-agent': 'deldelak-gateway' }, signal: AbortSignal.timeout(4000) })
          .then((r) => (r.ok ? r.json() : null)).catch(() => null);
      },
      canStop() {
        const base = self._baseCache;
        if (!base) return Promise.resolve(null);
        return fetch(base + '/internal/can-stop', { headers: { 'user-agent': 'deldelak-gateway' }, signal: AbortSignal.timeout(4000) })
          .then((r) => (r.ok ? r.json() : null)).catch(() => null);
      },
      fetch: (init, url) => fetch(url, init),
    };

    const storage = {
      get: (k, d) => state.storage.get(k).then((v) => (v === undefined ? d : v)),
      put: (k, v) => state.storage.put(k, v),
    };

    this.controller = createController({
      gh: new GitHubCodespaces(env),
      backend: lazyBackend,
      storage,
      crypto: SHA,
      cfg: {
        idleShutdownMinutes: Number(env.IDLE_SHUTDOWN_MINUTES || 10),
        holdSeconds: Number(env.HOLD_SECONDS || 75),
        startTimeoutMin: Number(env.START_TIMEOUT_MIN || 12),
      },
      onAlarmSchedule: (ts) => {
        if (ts) state.storage.setAlarm(ts).catch(() => {});
        else state.storage.deleteAlarm().catch(() => {});
      },
      log: (m) => console.log('[deldelak-do]', m),
    });
  }

  async _ensureLoaded() {
    if (this._loaded) return;
    this._loaded = true;
    this._baseCache = await this.state.storage.get('baseUrl', null);
    await this.controller.loadPersisted();
  }

  async fetch(request) {
    await this._ensureLoaded();
    return this.controller.handleRequest(request);
  }

  async alarm() {
    await this._ensureLoaded();
    return this.controller.onAlarm();
  }
}

/* ----------------------------------------------------------- worker init */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    // CORS preflight for browser clients calling the gateway directly
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        status: 204,
        headers: {
          'access-control-allow-origin': '*',
          'access-control-allow-methods': 'GET, POST, PUT, DELETE, OPTIONS',
          'access-control-allow-headers': 'authorization, content-type, x-api-key, x-request-id, x-gateway-secret, x-requested-with',
          'access-control-max-age': '86400',
        },
      });
    }
    try {
      const id = env.INSTANCE.idFromName('deldelak-instance');
      const stub = env.INSTANCE.get(id);
      const res = await stub.fetch(request);
      const out = new Response(res.body, res);
      out.headers.set('access-control-allow-origin', '*');
      return out;
    } catch (e) {
      console.error('[deldelak-worker] unhandled:', e && e.stack);
      return new Response(JSON.stringify({
        error: { message: 'gateway internal error', type: 'gateway_error' },
      }, null, 0), { status: 500, headers: { 'content-type': 'application/json' } });
    }
  },

  // safety net cron: enforce idle shutdown; NOT a keep-alive (mission §3)
  async scheduled(event, env, ctx) {
    const id = env.INSTANCE.idFromName('deldelak-instance');
    const stub = env.INSTANCE.get(id);
    await stub.fetch(new Request('https://gateway.internal/healthz'));
    // alarm() does the actual work on its own schedule; this tick just makes
    // sure a lost alarm is re-armed after deployments
    ctx.waitUntil(Promise.resolve());
  },
};
