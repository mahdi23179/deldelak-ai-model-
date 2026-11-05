/* ==========================================================================
   Deldelak - service status panel + owner key management (vanilla JS).
   Loaded alongside app.js. Does NOT rewrite the existing UI; it only fills
   the two containers declared in index.html (#svcStatusBody, #keyOwnerBox).

   Security notes (mission §3):
   - the GitHub OAuth token is never visible to this script: the server sets
     an HttpOnly owner-session cookie; JS only asks "am I the owner?"
   - reveal/rotate/revoke need X-Requested-With (CSRF) + same-origin cookies
   - the API key is shown on click only and never logged
   ========================================================================== */
(() => {
  'use strict';

  const fa = document.documentElement.lang !== 'en';
  const T = fa ? {
    svcTitle: 'وضعیت سرویس',
    codespace: 'وضعیت Codespace',
    model: 'وضعیت مدل',
    loaded: 'بارگیری‌شده',
    loading: 'در حال بارگیری',
    notLoaded: 'بارگیری نشده',
    active: 'درخواست‌های فعال',
    queued: 'درخواست‌های منتظر',
    lastActivity: 'آخرین فعالیت',
    autoStop: 'خاموشی خودکار تا',
    coldStart: 'برآورد Cold Start (اندازه‌گیری واقعی)',
    none: '—',
    err: 'خطا: ',
    gateway: 'گیت‌وی',
    direct: 'دسترسی مستقیم (بدون گیت‌وی)',
    ownerTitle: 'مدیریت کلید (مالک مخزن)',
    login: 'ورود با گیت‌هاب',
    loggedIn: 'وارد شده به‌عنوان',
    reveal: 'نمایش کلید',
    hide: 'پنهان کردن',
    rotate: 'ساخت کلید جدید (چرخش)',
    revoke: 'ابطال کلید فعلی',
    copy: 'کپی',
    hint: 'بازیابی کلید فقط با حساب GitHub مالک ممکن است. کلید در لاگ‌ها ثبت نمی‌شود.',
    rotating: 'کلید قبلی باطل شود و کلید جدید ساخته شود؟',
    revoking: 'کلید فعلی ابطال شود؟ باید دوباره آن را نمایش بگیرید.',
    okDone: 'انجام شد',
    sessionMissing: 'نشست مالک منقضی شده؛ دوباره وارد شوید.',
    oauthOff: 'ورود با گیت‌هاب تنظیم نشده (secretهای OAuth را در Codespace تعریف کنید).',
    stateNames: {
      STOPPED: 'سرور خاموش', WAKING: 'در حال روشن‌شدن', STARTING: 'در حال راه‌اندازی',
      LOADING_MODEL: 'در حال بارگیری مدل', READY: 'آماده', BUSY: 'در حال پردازش',
      IDLE: 'آماده (بی‌کار)', STOPPING: 'در حال خاموش‌شدن', ERROR: 'خطا', unknown: 'نامشخص',
    },
  } : {
    svcTitle: 'Service status',
    codespace: 'Codespace state',
    model: 'Model state',
    loaded: 'Loaded', loading: 'Loading', notLoaded: 'Not loaded',
    active: 'Active requests', queued: 'Queued requests',
    lastActivity: 'Last activity', autoStop: 'Auto-stop in',
    coldStart: 'Cold start estimate (measured)', none: '—', err: 'Error: ',
    gateway: 'Gateway', direct: 'Direct connection (no gateway)',
    ownerTitle: 'Key management (repo owner)',
    login: 'Sign in with GitHub', loggedIn: 'Signed in as',
    reveal: 'Reveal key', hide: 'Hide', rotate: 'Rotate key', revoke: 'Revoke key',
    copy: 'Copy', hint: 'Key recovery is owner-only via GitHub. Keys are never logged.',
    rotating: 'Revoke the old key and generate a new one?',
    revoking: 'Revoke the current key? You will have to reveal the new one.',
    okDone: 'Done', sessionMissing: 'Owner session expired; sign in again.',
    oauthOff: 'GitHub sign-in is not configured (set the OAuth secrets).',
    stateNames: {
      STOPPED: 'Stopped', WAKING: 'Waking up', STARTING: 'Starting',
      LOADING_MODEL: 'Loading model', READY: 'Ready', BUSY: 'Processing',
      IDLE: 'Ready (idle)', STOPPING: 'Stopping', ERROR: 'Error', unknown: 'Unknown',
    },
  };

  const $ = (s, r) => (r || document).querySelector(s);

  function fmtTime(ts) {
    if (!ts) return T.none;
    try {
      const ms = typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(ts);
      return new Date(ms).toLocaleString(fa ? 'fa-IR' : 'en-GB');
    } catch (e) { return String(ts); }
  }

  function fmtDur(sec) {
    if (sec == null) return T.none;
    const s = Math.max(0, Math.round(sec));
    if (s < 60) return s + 's';
    const m = Math.floor(s / 60);
    return m + 'm ' + (s % 60) + 's';
  }

  async function jfetch(url, opts = {}) {
    const res = await fetch(url, {
      credentials: 'same-origin',
      headers: { 'x-requested-with': 'XMLHttpRequest', ...(opts.headers || {}) },
      ...opts,
    });
    const ct = res.headers.get('content-type') || '';
    const body = ct.includes('json') ? await res.json() : await res.text();
    return { ok: res.ok, status: res.status, body };
  }

  /* ------------------------------- status ------------------------------- */

  function renderStatus(j) {
    const el = $('#svcStatusBody');
    if (!el) return;
    if (!j) { el.textContent = T.err + 'network'; return; }
    const g = j.gateway || null;
    const gwState = g ? (T.stateNames[g.state] || g.state) : null;
    const csState = j.codespace_state ? (T.stateNames[j.codespace_state] || j.codespace_state) : T.none;
    const b = j.backend || {};
    const modelState = b.model_loaded ? T.loaded : (b.model_loading ? T.loading : T.notLoaded);
    const rows = [
      [T.gateway + ' / ' + (g ? T.gateway : T.direct), gwState || T.direct],
      [T.codespace, csState],
      [T.model, modelState],
      [T.active, b.active_requests != null ? String(b.active_requests) : (g ? String(g.active_requests) : T.none)],
      [T.queued, b.queued_requests != null ? String(b.queued_requests) : (g ? String(g.queued_requests) : T.none)],
      [T.lastActivity, g ? fmtTime(g.last_activity) : fmtTime(b.last_activity)],
    ];
    if (g && g.auto_stop_at) rows.push([T.autoStop, fmtTime(g.auto_stop_at)]);
    if (g && g.cold_start_estimate_s != null) rows.push([T.coldStart, fmtDur(g.cold_start_estimate_s)]);
    if (b.last_error) rows.push([T.err, String(b.last_error).slice(0, 120)]);
    el.innerHTML = '<table class="kv-table">' + rows.map(([k, v]) =>
      '<tr><td>' + k + '</td><td class="mono" dir="auto">' + v + '</td></tr>').join('') + '</table>'
      + (g && g.last_stop_error ? '<p class="hint">' + T.err + g.last_stop_error + '</p>' : '');
  }

  async function pollStatus() {
    try { renderStatus(await jfetch('/health', { headers: {} })); }
    catch (e) { renderStatus(null); }
  }

  /* ------------------------------ key admin ----------------------------- */

  function ownerBoxHtml(session) {
    if (!session) return '<p class="hint">…</p>';
    if (!session.oauth_configured) {
      return '<p class="hint">' + T.oauthOff + '</p>';
    }
    if (!session.is_owner) {
      return '<a class="btn btn-ghost btn-sm" href="/api/key/oauth/start">' + T.login + '</a>'
        + '<p class="hint">' + T.hint + '</p>';
    }
    return '<p class="hint">' + T.loggedIn + ' <strong>' + session.login + '</strong></p>'
      + '<div class="row wrap owner-actions">'
      + '<button id="revealKeyBtn" class="btn btn-ghost btn-sm" type="button">' + T.reveal + '</button>'
      + '<button id="copyRevealedBtn" class="btn btn-ghost btn-sm" type="button" hidden>' + T.copy + '</button>'
      + '<button id="rotateKeyBtn2" class="btn btn-danger-ghost btn-sm" type="button">' + T.rotate + '</button>'
      + '<button id="revokeKeyBtn" class="btn btn-danger-ghost btn-sm" type="button">' + T.revoke + '</button>'
      + '</div>'
      + '<code id="revealedKey" class="mono key-mask" dir="ltr" hidden></code>'
      + '<p class="hint">' + T.hint + '</p>';
  }

  function bindKeyActions() {
    const box = $('#keyOwnerBox');
    if (!box) return;
    const revealBtn = $('#revealKeyBtn');
    if (!revealBtn) return;
    let shown = false;
    revealBtn.addEventListener('click', async () => {
      const out = $('#revealedKey');
      const copyBtn = $('#copyRevealedBtn');
      if (shown) { out.hidden = true; copyBtn.hidden = true; shown = false; revealBtn.textContent = T.reveal; return; }
      const r = await jfetch('/api/key/reveal', { method: 'POST' });
      if (!r.ok) {
        alert(r.status === 401 ? T.sessionMissing : (r.body && r.body.detail) || T.err);
        if (r.status === 401) loadOwner();
        return;
      }
      out.textContent = r.body.key;
      out.hidden = false;
      copyBtn.hidden = false;
      shown = true;
      revealBtn.textContent = T.hide;
      copyBtn.onclick = () => navigator.clipboard.writeText(r.body.key).catch(() => {});
    });
    $('#rotateKeyBtn2').addEventListener('click', async () => {
      if (!window.confirm(T.rotating)) return;
      const r = await jfetch('/api/key/rotate', { method: 'POST' });
      if (!r.ok) { alert(r.status === 401 ? T.sessionMissing : T.err); if (r.status === 401) loadOwner(); return; }
      const out = $('#revealedKey');
      out.textContent = r.body.key;
      out.hidden = false;
      shown = true;
      $('#revealKeyBtn').textContent = T.hide;
      alert(T.okDone);
    });
    $('#revokeKeyBtn').addEventListener('click', async () => {
      if (!window.confirm(T.revoking)) return;
      const r = await jfetch('/api/key/revoke', { method: 'POST' });
      if (!r.ok) { alert(r.status === 401 ? T.sessionMissing : T.err); if (r.status === 401) loadOwner(); return; }
      const out = $('#revealedKey');
      out.hidden = true;
      shown = false;
      $('#revealKeyBtn').textContent = T.reveal;
      alert(T.okDone);
    });
  }

  async function loadOwner() {
    const box = $('#keyOwnerBox');
    if (!box) return;
    try {
      const { body } = await jfetch('/api/key/session', { headers: {} });
      box.innerHTML = ownerBoxHtml(body);
      bindKeyActions();
    } catch (e) {
      box.innerHTML = '<p class="hint">' + T.err + e.message + '</p>';
    }
  }

  /* -------------------------------- boot -------------------------------- */

  function boot() {
    if ($('#svcStatusBody')) pollStatus();
    setInterval(() => { if ($('#svcStatusBody')) pollStatus(); }, 20000);
    if ($('#keyOwnerBox')) loadOwner();
    // owner returns from the OAuth callback to /?owner_session=1
    if (new URLSearchParams(window.location.search).get('owner_session') === '1') {
      window.history.replaceState({}, '', '/');
      loadOwner();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
