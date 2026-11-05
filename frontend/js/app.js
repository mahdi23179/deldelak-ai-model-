/* ==========================================================================
   Deldelak AI Model - frontend application (vanilla JS, no build step)
   Persian RTL default + fa/en toggle, GitHub dark palette.
   All icons are inline SVG. No emoji anywhere.
   ========================================================================== */
(() => {
  'use strict';

  /* ------------------------------ tiny helpers ---------------------------- */

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));

  const ls = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
  };

  const state = {
    lang: ls.get('deldelak_lang') || 'fa',
    key: ls.get('deldelak_key') || '',
    view: 'chat',
    model: null,
    origin: (window.location.origin && window.location.origin !== 'null')
      ? window.location.origin : 'http://localhost:8000',
    // chat
    sessions: [],
    sessionId: null,
    messages: [],
    streaming: false,
    abort: null,
    // agent
    tasks: [],
    taskDetail: null,
    selectedTask: null,
    openSteps: {},
    agentTimer: null,
    // files
    filesPath: '.',
    fileItems: [],
    editorPath: null,
    // projects / system
    projects: [],
    systemData: null,
    systemTimer: null
  };

  const ACTIVE_STATUSES = ['queued', 'planning', 'executing', 'verifying', 'awaiting_approval'];

  /* ------------------------------ i18n ------------------------------------ */

  const I18N = {
    fa: {
      'nav.chat': 'چت', 'nav.agent': 'عامل', 'nav.files': 'فایل‌ها',
      'nav.projects': 'پروژه‌ها', 'nav.system': 'سیستم', 'nav.settings': 'تنظیمات',
      'status.ok': 'آماده', 'status.degraded': 'در حال آماده‌سازی', 'status.offline': 'قطع',
      'status.busy': 'در حال پردازش', 'status.loading_model': 'در حال بارگیری مدل',
      'status.stopped': 'سرور خاموش', 'status.waking': 'در حال روشن‌شدن',
      'status.starting': 'در حال راه‌اندازی', 'status.stopping': 'در حال خاموش‌شدن', 'status.error': 'خطا',
      'key.owner': 'ورود مالک با گیت‌هاب (بازیابی کلید)',
      'chat.new': 'گفتگوی جدید', 'chat.newTitle': 'گفتگوی جدید',
      'chat.placeholder': 'پیام خود را بنویسید…', 'chat.send': 'ارسال', 'chat.stop': 'توقف',
      'chat.empty': 'هنوز پیامی نیست', 'chat.emptyHint': 'اولین پیام را بفرستید تا گفتگو شروع شود',
      'chat.you': 'شما', 'chat.assistant': 'دلدلک', 'chat.stopped': 'متوقف شد',
      'chat.deleteSession': 'حذف گفتگو', 'chat.delConfirm': 'این گفتگو برای همیشه حذف شود؟',
      'chat.noSessions': 'گفتگویی نیست',
      'agent.instruction': 'دستور تسک',
      'agent.instructionPh': 'مثلاً: یک پروژه ماشین‌حساب با پایتون بساز و برایش تست بنویس',
      'agent.mode': 'حالت', 'mode.auto': 'خودکار', 'mode.coding': 'کدنویسی', 'mode.bash': 'بش',
      'agent.run': 'اجرای تسک', 'agent.tasks': 'تسک‌ها', 'agent.refresh': 'بازخوانی',
      'agent.noTasks': 'هنوز تسکی ثبت نشده است',
      'agent.noDetail': 'برای دیدن جزئیات، یک تسک را انتخاب کنید',
      'agent.cycles': 'چرخه', 'agent.steps': 'گام‌ها',
      'agent.summary': 'خلاصه نتیجه', 'agent.errorLabel': 'خطا',
      'agent.approvalTitle': 'نیاز به تأیید شما',
      'agent.approvalBody': 'عامل برای ادامه عملیات منتظر تأیید شماست.',
      'agent.approve': 'تأیید', 'agent.deny': 'رد',
      'agent.output': 'خروجی', 'agent.noOutput': 'خروجی ثبت نشده است',
      'status.queued': 'در صف', 'status.planning': 'برنامه‌ریزی', 'status.executing': 'در حال اجرا',
      'status.verifying': 'در حال بررسی', 'status.awaiting_approval': 'در انتظار تأیید',
      'status.completed': 'تکمیل شد', 'status.completed_with_issues': 'تکمیل با اشکال', 'status.failed': 'ناموفق',
      'step.pending': 'در انتظار', 'step.running': 'در حال اجرا', 'step.awaiting_approval': 'در انتظار تأیید',
      'step.ok': 'موفق', 'step.error': 'خطا',
      'phase.plan': 'برنامه‌ریزی', 'phase.execute': 'اجرا', 'phase.verify': 'بررسی', 'phase.approve': 'نقطهٔ تأیید',
      'files.root': 'فضای کاری', 'files.newFile': 'فایل جدید', 'files.newFolder': 'پوشهٔ جدید',
      'files.refresh': 'بازخوانی', 'files.empty': 'این پوشه خالی است',
      'files.save': 'ذخیره', 'files.delete': 'حذف', 'files.copyPath': 'کپی مسیر',
      'files.confirmDelete': 'آیا از حذف این مسیر مطمئن هستید؟', 'files.namePrompt': 'نام مورد نظر را وارد کنید:',
      'projects.new': 'پروژهٔ جدید', 'projects.name': 'نام پروژه (لاتین)', 'projects.template': 'قالب',
      'tpl.python': 'پایتون', 'tpl.node': 'Node.js', 'projects.create': 'ساخت پروژه',
      'projects.empty': 'هنوز پروژه‌ای ساخته نشده است', 'projects.emptyHint': 'با دکمهٔ پروژهٔ جدید شروع کنید',
      'projects.files': 'فایل', 'projects.run': 'اجرا', 'projects.test': 'تست', 'projects.export': 'خروجی ZIP',
      'projects.exitCode': 'کد خروج',
      'system.ram': 'حافظهٔ رم', 'system.cpu': 'پردازنده', 'system.disk': 'دیسک',
      'system.load': 'بار میانگین', 'system.model': 'مدل زبانی',
      'system.loaded': 'بارگذاری‌شده', 'system.notLoaded': 'بارگذاری‌نشده',
      'system.modelId': 'شناسه مدل', 'system.size': 'حجم فایل مدل', 'system.rss': 'مصرف حافظهٔ llama',
      'system.ctx': 'طول زمینه', 'system.threads': 'رشته‌ها', 'system.tps': 'توکن/ثانیه',
      'system.agent': 'عامل', 'system.activeTasks': 'تسک‌های فعال', 'system.worker': 'worker پس‌زمینه',
      'system.on': 'فعال', 'system.off': 'غیرفعال',
      'system.codespace': 'کداسپیس', 'system.region': 'منطقه', 'system.uptime': 'مدت کارکرد',
      'system.platform': 'سامانه میزبان', 'system.os': 'سیستم‌عامل', 'system.cpus': 'هستهٔ CPU',
      'system.python': 'پایتون', 'system.version': 'نسخه', 'system.refresh': 'بازخوانی',
      'settings.key': 'کلید API', 'settings.rotate': 'چرخش کلید',
      'settings.rotateConfirm': 'کلید جدید ساخته شود؟ کلید قبلی باطل خواهد شد.',
      'settings.keyNote': 'این کلید را محرمانه نگه دارید؛ با چرخش، کلید قبلی باطل می‌شود.',
      'settings.snippets': 'نمونهٔ کد استفاده از API',
      'settings.python': 'Python (کتابخانه requests)', 'settings.js': 'JavaScript (fetch)',
      'settings.language': 'زبان رابط', 'settings.theme': 'پوسته',
      'settings.themeInfo': 'تم تیره با پالت رسمی GitHub به‌صورت پیش‌فرض فعال است.',
      'settings.autowake': 'راهنمای Auto-Wake',
      'settings.autowakeNote': 'Auto-Wake از طریق GitHub Actions در مخزن پروژه تنظیم می‌شود؛ جزئیات در سند API آمده است.',
      'settings.autowakeLink': 'مشاهدهٔ سند API',
      'key.title': 'ورود با کلید API', 'key.needed': 'برای استفاده از دلدلک، کلید API را وارد کنید.',
      'key.hint': 'کلید در فایل .state/api_key یا لاگ راه‌اندازی سرور است.',
      'key.connect': 'اتصال', 'key.invalid': 'کلید نامعتبر است؛ دوباره تلاش کنید.',
      'common.copy': 'کپی', 'common.close': 'بستن', 'common.loading': 'در حال بارگذاری…',
      'common.none': '—', 'common.cancel': 'انصراف',
      'ok.copied': 'کپی شد', 'ok.saved': 'ذخیره شد', 'ok.deleted': 'حذف شد', 'ok.created': 'ساخته شد',
      'ok.rotated': 'کلید با موفقیت عوض شد', 'ok.taskCreated': 'تسک با موفقیت ثبت شد',
      'ok.approved': 'عملیات تأیید شد', 'ok.denied': 'عملیات رد شد',
      'ok.sessionNew': 'گفتگوی جدید ساخته شد', 'ok.connected': 'اتصال برقرار شد',
      'err.network': 'خطای شبکه: سرور در دسترس نیست', 'err.http': 'خطای سرور',
      'err.auth': 'دسترسی نامعتبر؛ کلید را بررسی کنید', 'err.fill': 'مقدار معتبری وارد کنید',
      'err.copy': 'کپی ممکن نشد',
      'unit.d': 'روز', 'unit.h': 'ساعت', 'unit.m': 'دقیقه', 'unit.s': 'ثانیه'
    },
    en: {
      'nav.chat': 'Chat', 'nav.agent': 'Agent', 'nav.files': 'Files',
      'nav.projects': 'Projects', 'nav.system': 'System', 'nav.settings': 'Settings',
      'status.ok': 'Ready', 'status.degraded': 'Warming up', 'status.offline': 'Offline',
      'status.busy': 'Processing', 'status.loading_model': 'Loading model',
      'status.stopped': 'Server stopped', 'status.waking': 'Waking up',
      'status.starting': 'Starting', 'status.stopping': 'Stopping', 'status.error': 'Error',
      'key.owner': 'Owner login with GitHub (recover key)',
      'chat.new': 'New chat', 'chat.newTitle': 'New chat',
      'chat.placeholder': 'Type your message…', 'chat.send': 'Send', 'chat.stop': 'Stop',
      'chat.empty': 'No messages yet', 'chat.emptyHint': 'Send the first message to start the conversation',
      'chat.you': 'You', 'chat.assistant': 'Deldelak', 'chat.stopped': 'Stopped',
      'chat.deleteSession': 'Delete chat', 'chat.delConfirm': 'Delete this conversation permanently?',
      'chat.noSessions': 'No conversations',
      'agent.instruction': 'Task instruction',
      'agent.instructionPh': 'e.g. build a Python calculator project and write tests for it',
      'agent.mode': 'Mode', 'mode.auto': 'Auto', 'mode.coding': 'Coding', 'mode.bash': 'Bash',
      'agent.run': 'Run task', 'agent.tasks': 'Tasks', 'agent.refresh': 'Refresh',
      'agent.noTasks': 'No tasks yet',
      'agent.noDetail': 'Select a task to see its details',
      'agent.cycles': 'Cycles', 'agent.steps': 'Steps',
      'agent.summary': 'Result summary', 'agent.errorLabel': 'Error',
      'agent.approvalTitle': 'Your approval is required',
      'agent.approvalBody': 'The agent is waiting for your approval to continue.',
      'agent.approve': 'Approve', 'agent.deny': 'Deny',
      'agent.output': 'Output', 'agent.noOutput': 'No output recorded',
      'status.queued': 'Queued', 'status.planning': 'Planning', 'status.executing': 'Executing',
      'status.verifying': 'Verifying', 'status.awaiting_approval': 'Awaiting approval',
      'status.completed': 'Completed', 'status.completed_with_issues': 'Completed with issues', 'status.failed': 'Failed',
      'step.pending': 'Pending', 'step.running': 'Running', 'step.awaiting_approval': 'Awaiting approval',
      'step.ok': 'OK', 'step.error': 'Error',
      'phase.plan': 'Planning', 'phase.execute': 'Execution', 'phase.verify': 'Verification', 'phase.approve': 'Approval gate',
      'files.root': 'Workspace', 'files.newFile': 'New file', 'files.newFolder': 'New folder',
      'files.refresh': 'Refresh', 'files.empty': 'This folder is empty',
      'files.save': 'Save', 'files.delete': 'Delete', 'files.copyPath': 'Copy path',
      'files.confirmDelete': 'Delete this path?', 'files.namePrompt': 'Enter a name:',
      'projects.new': 'New project', 'projects.name': 'Project name (latin)', 'projects.template': 'Template',
      'tpl.python': 'Python', 'tpl.node': 'Node.js', 'projects.create': 'Create project',
      'projects.empty': 'No projects yet', 'projects.emptyHint': 'Start with the New project button',
      'projects.files': 'files', 'projects.run': 'Run', 'projects.test': 'Test', 'projects.export': 'ZIP export',
      'projects.exitCode': 'Exit code',
      'system.ram': 'RAM', 'system.cpu': 'CPU', 'system.disk': 'Disk',
      'system.load': 'Load avg', 'system.model': 'LLM model',
      'system.loaded': 'Loaded', 'system.notLoaded': 'Not loaded',
      'system.modelId': 'Model id', 'system.size': 'Model file size', 'system.rss': 'llama RSS memory',
      'system.ctx': 'Context', 'system.threads': 'Threads', 'system.tps': 'tokens/sec',
      'system.agent': 'Agent', 'system.activeTasks': 'Active tasks', 'system.worker': 'Background worker',
      'system.on': 'On', 'system.off': 'Off',
      'system.codespace': 'Codespace', 'system.region': 'Region', 'system.uptime': 'Uptime',
      'system.platform': 'Host platform', 'system.os': 'OS', 'system.cpus': 'CPU cores',
      'system.python': 'Python', 'system.version': 'Version', 'system.refresh': 'Refresh',
      'settings.key': 'API key', 'settings.rotate': 'Rotate key',
      'settings.rotateConfirm': 'Generate a new key? The old key will be revoked.',
      'settings.keyNote': 'Keep this key secret; rotating revokes the previous key.',
      'settings.snippets': 'API usage snippets',
      'settings.python': 'Python (requests)', 'settings.js': 'JavaScript (fetch)',
      'settings.language': 'UI language', 'settings.theme': 'Theme',
      'settings.themeInfo': 'Dark theme with the official GitHub palette is enabled by default.',
      'settings.autowake': 'Auto-Wake guide',
      'settings.autowakeNote': 'Auto-Wake is configured through GitHub Actions in the repository; see the API docs.',
      'settings.autowakeLink': 'Open API docs',
      'key.title': 'Sign in with API key', 'key.needed': 'Enter your Deldelak API key to continue.',
      'key.hint': 'The key is in the .state/api_key file or in the server startup log.',
      'key.connect': 'Connect', 'key.invalid': 'Invalid key; try again.',
      'common.copy': 'Copy', 'common.close': 'Close', 'common.loading': 'Loading…',
      'common.none': '—', 'common.cancel': 'Cancel',
      'ok.copied': 'Copied', 'ok.saved': 'Saved', 'ok.deleted': 'Deleted', 'ok.created': 'Created',
      'ok.rotated': 'Key rotated successfully', 'ok.taskCreated': 'Task created',
      'ok.approved': 'Approved', 'ok.denied': 'Denied',
      'ok.sessionNew': 'New chat created', 'ok.connected': 'Connected',
      'err.network': 'Network error: server unreachable', 'err.http': 'Server error',
      'err.auth': 'Invalid access; check your key', 'err.fill': 'Please enter a valid value',
      'err.copy': 'Copy failed',
      'unit.d': 'd', 'unit.h': 'h', 'unit.m': 'min', 'unit.s': 's'
    }
  };

  const t = (k) => {
    const d = I18N[state.lang] || I18N.fa;
    return (k in d) ? d[k] : (I18N.fa[k] != null ? I18N.fa[k] : k);
  };

  /* ------------------------------ inline SVG icons ------------------------- */

  const ICONS = {
    chat: '<path d="M3.5 6.2c0-1.5 1-2.7 2.5-2.7h8c1.5 0 2.5 1.2 2.5 2.7v5.1c0 1.5-1 2.7-2.5 2.7H9.2l-4 3v-3.1c-1-.3-1.7-1.4-1.7-2.6Z"/>',
    agent: '<rect x="5" y="5" width="10" height="10" rx="1.5"/><rect x="8.2" y="8.2" width="3.6" height="3.6" rx=".8"/><path d="M8 2.5v2M12 2.5v2M8 15.5v2M12 15.5v2M2.5 8h2M2.5 12h2M15.5 8h2M15.5 12h2"/>',
    folder: '<path d="M3 6.1c0-1 .7-1.8 1.7-1.8h3l1.7 1.9h5.9c1 0 1.7.8 1.7 1.8v6.4c0 1-.7 1.8-1.7 1.8H4.7c-1 0-1.7-.8-1.7-1.8Z"/>',
    projects: '<path d="M10 2.4 17 6v8l-7 3.6L3 14V6Z"/><path d="M3.4 6.2 10 9.7l6.6-3.5M10 9.7V17"/>',
    system: '<path d="M2.8 10h3.4l2-5.5 3.6 11 2-5.5h3.4"/>',
    file: '<path d="M6 2.8h5.2L15 6.6v10.6H6Z"/><path d="M11 2.8v4h4"/>',
    copy: '<rect x="7.5" y="7.5" width="9" height="9" rx="1.5"/><path d="M5 12.5h-.5A1.5 1.5 0 0 1 3 11V5.5A1.5 1.5 0 0 1 4.5 4H10a1.5 1.5 0 0 1 1.5 1.5V6"/>',
    close: '<path d="M5 5l10 10M15 5 5 15"/>',
    refresh: '<path d="M16.5 10a6.5 6.5 0 1 1-1.9-4.6M16.5 2.8v3.2h-3.2"/>',
    check: '<path d="M4 10.5l4 4 8-9"/>',
    alert: '<path d="M10 3.2 18 16.5H2Z"/><path d="M10 8v3.6M10 14.2v.2"/>',
    info: '<circle cx="10" cy="10" r="7"/><path d="M10 9.2v4.3M10 6.4v.2"/>',
    clock: '<circle cx="10" cy="10" r="7"/><path d="M10 6v4.2l2.8 1.6"/>',
    chevronDown: '<path d="M5.5 8 10 12.5 14.5 8"/>',
    chevron: '<path d="M8 4.5 13.5 10 8 15.5"/>'
  };

  function icon(name) {
    return '<svg class="ic" viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" '
      + 'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
      + (ICONS[name] || ICONS.info) + '</svg>';
  }

  /* ------------------------------ toast + clipboard ------------------------ */

  function toast(msg, kind, ms) {
    const box = $('#toasts');
    if (!box) return;
    const el = document.createElement('div');
    el.className = 'toast toast-' + (kind || 'info');
    const icName = kind === 'error' ? 'alert' : (kind === 'success' ? 'check' : 'info');
    el.innerHTML = icon(icName) + '<span></span>';
    el.querySelector('span').textContent = msg;
    box.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => {
      el.classList.remove('show');
      setTimeout(() => el.remove(), 250);
    }, ms || 4200);
  }

  function warn(e) {
    if (e && e.status === 401) return; // key modal already opened by apiFetch
    toast((e && e.message) || t('err.network'), 'error');
  }

  function copyText(txt) {
    const val = String(txt == null ? '' : txt);
    const done = (ok) => toast(ok ? t('ok.copied') : t('err.copy'), ok ? 'success' : 'error');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(val).then(() => done(true), () => fallbackCopy(val, done));
    } else {
      fallbackCopy(val, done);
    }
  }

  function fallbackCopy(val, done) {
    try {
      const ta = document.createElement('textarea');
      ta.value = val;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      done(!!ok);
    } catch (e) { done(false); }
  }

  /* ------------------------------ modal ------------------------------------ */

  function modalShell(titleText, bodyHtml) {
    const root = $('#modalRoot');
    root.innerHTML = '';
    const ov = document.createElement('div');
    ov.className = 'modal-overlay';
    ov.innerHTML =
      '<div class="modal panel" role="dialog" aria-modal="true">'
      + '<div class="modal-head"><h3>' + esc(titleText) + '</h3>'
      + '<button type="button" class="btn btn-ghost icon-only modal-close" aria-label="' + esc(t('common.close')) + '">'
      + icon('close') + '</button></div>'
      + '<div class="modal-body">' + bodyHtml + '</div></div>';
    root.appendChild(ov);
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    const close = () => { ov.remove(); document.removeEventListener('keydown', onKey); };
    document.addEventListener('keydown', onKey);
    ov.querySelector('.modal-close').addEventListener('click', close);
    ov.addEventListener('mousedown', (e) => { if (e.target === ov) close(); });
    return { ov, close };
  }

  function closeModal() {
    const ov = $('#modalRoot .modal-overlay');
    if (ov) ov.remove();
  }

  function emptyStateHTML(icName, title, sub) {
    return '<div class="empty-state">' + icon(icName)
      + '<p class="empty-title">' + esc(title) + '</p>'
      + (sub ? '<p class="small muted">' + esc(sub) + '</p>' : '')
      + '</div>';
  }

  /* ------------------------------ formatting ------------------------------- */

  function fmtBytes(n) {
    if (n == null || isNaN(n)) return t('common.none');
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
    return (n / 1073741824).toFixed(2) + ' GB';
  }

  function fmtUptime(s) {
    s = Math.max(0, Math.floor(s || 0));
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const parts = [];
    if (d) parts.push(d + ' ' + t('unit.d'));
    if (h) parts.push(h + ' ' + t('unit.h'));
    if (m) parts.push(m + ' ' + t('unit.m'));
    if (!d && !h) parts.push(sec + ' ' + t('unit.s'));
    return parts.join(' ') || ('0 ' + t('unit.s'));
  }

  function clampPct(p) { return Math.max(0, Math.min(100, Number(p) || 0)); }

  function barCls(p) {
    p = Number(p) || 0;
    if (p >= 90) return 'bar-red';
    if (p >= 70) return 'bar-yellow';
    return 'bar-accent';
  }

  /* ------------------------------ API layer -------------------------------- */

  function ApiError(status, message) {
    this.status = status;
    this.message = message;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function authHeaders(extra) {
    const h = Object.assign({}, extra || {});
    if (state.key) {
      h['Authorization'] = 'Bearer ' + state.key;
      h['X-API-Key'] = state.key;
    }
    return h;
  }

  async function parseErr(res) {
    let msg = '';
    try {
      const j = await res.json();
      if (typeof j.detail === 'string') msg = j.detail;
      else if (j.detail && typeof j.detail === 'object') msg = j.detail.msg || j.detail.message || '';
      else if (j.error && j.error.message) msg = j.error.message;
      else if (typeof j.message === 'string') msg = j.message;
    } catch (e) { /* body was not json */ }
    return msg || (t('err.http') + ' (' + res.status + ')');
  }

  async function apiFetch(path, opts) {
    opts = opts || {};
    const headers = authHeaders(opts.headers);
    let body = opts.body;
    if (body != null && typeof body !== 'string') {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(state.origin + path, Object.assign({}, opts, { headers, body }));
    } catch (e) {
      throw new ApiError(0, t('err.network'));
    }
    if (res.status === 401) {
      openKeyModal(state.key);
      throw new ApiError(401, t('err.auth'));
    }
    if (!res.ok) throw new ApiError(res.status, await parseErr(res));
    const ct = res.headers.get('content-type') || '';
    if (ct.indexOf('application/json') !== -1) return res.json();
    return res.text();
  }

  /* ------------------------------ key onboarding --------------------------- */

  async function validateKey(k) {
    try {
      const res = await fetch(state.origin + '/v1/models', {
        headers: { 'Authorization': 'Bearer ' + k, 'X-API-Key': k }
      });
      return res.ok;
    } catch (e) { return false; }
  }

  async function initKey() {
    // 1) claim window: first caller receives the key
    try {
      const res = await fetch(state.origin + '/api/key');
      if (res.ok) {
        const j = await res.json();
        if (j && j.key) {
          state.key = j.key;
          ls.set('deldelak_key', j.key);
          claimOnServer(j.key);
          return true;
        }
      }
      // 403 => already claimed elsewhere
    } catch (e) { /* network failure (e.g. file://) */ }
    // 2) try a previously stored key
    if (state.key && await validateKey(state.key)) return true;
    // 3) ask the user
    openKeyModal('');
    return false;
  }

  function claimOnServer(k) {
    fetch(state.origin + '/api/key/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: k })
    }).catch(() => { /* best effort */ });
  }

  function openKeyModal(prefill) {
    closeModal();
    const m = modalShell(t('key.title'),
      '<p class="small muted">' + esc(t('key.needed')) + '</p>'
      + '<input id="keyInput" class="input mono" dir="ltr" autocomplete="off" spellcheck="false" '
      + 'placeholder="ddk-..." value="' + esc(prefill || '') + '">'
      + '<p class="hint">' + esc(t('key.hint')) + '</p>'
      + '<p id="keyErr" class="form-err" hidden></p>'
      + '<div class="modal-actions">'
      + '<button type="button" class="btn btn-primary" id="keyConnect">' + esc(t('key.connect')) + '</button>'
      + '<a type="button" class="btn btn-ghost" id="ownerOAuthBtn" href="/api/key/oauth/start">' + esc(t('key.owner')) + '</a>'
      + '</div>');
    const input = m.ov.querySelector('#keyInput');
    const errEl = m.ov.querySelector('#keyErr');
    const btn = m.ov.querySelector('#keyConnect');
    const fail = (msg) => { errEl.textContent = msg; errEl.hidden = false; btn.disabled = false; };
    const connect = async () => {
      const k = input.value.trim();
      errEl.hidden = true;
      if (!k) { fail(t('err.fill')); return; }
      btn.disabled = true;
      const ok = await validateKey(k);
      if (!ok) { fail(t('key.invalid')); return; }
      state.key = k;
      ls.set('deldelak_key', k);
      claimOnServer(k);
      closeModal();
      toast(t('ok.connected'), 'success');
      afterAuth();
    };
    btn.addEventListener('click', connect);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') connect(); });
    setTimeout(() => input.focus(), 60);
  }

  function afterAuth() {
    loadModelBadge();
    showView('chat');
  }

  /* ------------------------------ topbar ----------------------------------- */

  function startHealthPoll() {
    const ping = async () => {
      try {
        const res = await fetch(state.origin + '/health');
        setHealth(await res.json());
      } catch (e) { setHealth(null); }
    };
    ping();
    setInterval(ping, 15000);
  }

  function setHealth(j) {
    const dot = $('#statusDot');
    const txt = $('#statusText');
    if (!j) { dot.className = 'dot dot-red'; txt.textContent = t('status.offline'); return; }
    // gateway deep health: states read from reality (mission §7)
    const gs = j.gateway ? j.gateway.state : null;
    if (gs === 'STOPPED') { dot.className = 'dot dot-muted'; txt.textContent = t('status.stopped'); return; }
    if (gs === 'WAKING' || gs === 'STARTING') { dot.className = 'dot dot-yellow'; txt.textContent = t('status.waking'); return; }
    if (gs === 'LOADING_MODEL') { dot.className = 'dot dot-yellow'; txt.textContent = t('status.loading_model'); return; }
    if (gs === 'STOPPING') { dot.className = 'dot dot-yellow'; txt.textContent = t('status.stopping'); return; }
    if (gs === 'ERROR') { dot.className = 'dot dot-red'; txt.textContent = t('status.error'); return; }
    if (j.model_loaded) {
      dot.className = 'dot dot-green';
      txt.textContent = (j.status === 'busy') ? t('status.busy') : t('status.ok');
    } else if (j.model_loading) {
      dot.className = 'dot dot-yellow'; txt.textContent = t('status.loading_model');
    } else {
      dot.className = 'dot dot-yellow'; txt.textContent = t('status.degraded');
    }
  }

  async function loadModelBadge() {
    const b = $('#modelBadge');
    try {
      const j = await apiFetch('/v1/models');
      const m = (j.data && j.data[0]) || null;
      state.model = m;
      b.textContent = m && m.id ? m.id : t('common.none');
      b.title = m && m.label ? m.label : '';
    } catch (e) {
      b.textContent = t('common.none');
    }
  }

  /* ------------------------------ i18n apply ------------------------------- */

  function applyStaticI18n() {
    document.documentElement.lang = state.lang;
    document.documentElement.dir = state.lang === 'fa' ? 'rtl' : 'ltr';
    $$('[data-i18n]').forEach((el) => { el.textContent = t(el.getAttribute('data-i18n')); });
    $$('[data-i18n-ph]').forEach((el) => { el.setAttribute('placeholder', t(el.getAttribute('data-i18n-ph'))); });
    $$('[data-i18n-title]').forEach((el) => { el.setAttribute('title', t(el.getAttribute('data-i18n-title'))); });
    updateLangUI();
  }

  function updateLangUI() {
    $('#langToggle').textContent = state.lang === 'fa' ? 'EN' : 'فا';
    const fa = $('#langFa');
    const en = $('#langEn');
    if (fa && en) {
      fa.classList.toggle('active', state.lang === 'fa');
      en.classList.toggle('active', state.lang === 'en');
    }
  }

  function setLang(l) {
    state.lang = l === 'en' ? 'en' : 'fa';
    ls.set('deldelak_lang', state.lang);
    applyStaticI18n();
    refreshDynamic();
  }

  function refreshDynamic() {
    renderSessionSelect();
    if (!state.streaming) renderMessages();
    if (state.view === 'agent') { renderTaskList(); renderTaskDetail(); }
    if (state.view === 'files') { renderCrumbs(); renderFileList(); }
    if (state.view === 'projects') renderProjects();
    if (state.view === 'system') renderSystem();
    if (state.view === 'settings') renderSettings();
  }

  /* ------------------------------ view router ------------------------------ */

  function showView(v) {
    state.view = v;
    $$('.view').forEach((s) => { s.hidden = s.id !== 'view-' + v; });
    $$('.nav-btn').forEach((b) => { b.classList.toggle('active', b.getAttribute('data-view') === v); });
    if (v === 'chat') loadSessions();
    else if (v === 'agent') loadTasks();
    else if (v === 'files') loadDir(state.filesPath);
    else if (v === 'projects') loadProjects();
    else if (v === 'system') startSystem();
    else if (v === 'settings') renderSettings();
    if (v !== 'system') stopSystem();
    if (v !== 'agent') stopAgentPoll();
  }

  /* ==========================================================================
     CHAT
     ========================================================================== */

  async function loadSessions() {
    if (state.streaming) return;
    try {
      const j = await apiFetch('/v1/sessions');
      state.sessions = (j && j.data) || [];
    } catch (e) {
      state.sessions = [];
      warn(e);
    }
    renderSessionSelect();
    if (!state.sessions.length) {
      state.messages = [];
      renderMessages();
      return;
    }
    if (!state.sessionId || !state.sessions.some((s) => s.id === state.sessionId)) {
      state.sessionId = state.sessions[0].id;
    }
    await loadSessionMessages(state.sessionId);
  }

  function renderSessionSelect() {
    const sel = $('#sessionSelect');
    sel.innerHTML = '';
    if (!state.sessions.length) {
      const o = document.createElement('option');
      o.value = '';
      o.textContent = t('chat.noSessions');
      sel.appendChild(o);
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    state.sessions.forEach((s) => {
      const o = document.createElement('option');
      o.value = s.id;
      o.textContent = s.title || s.id;
      sel.appendChild(o);
    });
    sel.value = state.sessionId || state.sessions[0].id;
  }

  async function loadSessionMessages(sid) {
    try {
      const j = await apiFetch('/v1/sessions/' + encodeURIComponent(sid) + '/messages');
      state.messages = ((j && j.data) || []).map((m) => ({ role: m.role, content: m.content }));
    } catch (e) {
      state.messages = [];
      warn(e);
    }
    renderMessages();
  }

  async function newSession() {
    try {
      const s = await apiFetch('/v1/sessions', {
        method: 'POST',
        body: { title: t('chat.newTitle'), mode: 'chat' }
      });
      state.sessions.unshift(s);
      state.sessionId = s.id;
      state.messages = [];
      renderSessionSelect();
      renderMessages();
      toast(t('ok.sessionNew'), 'success');
    } catch (e) { warn(e); }
  }

  async function deleteSession() {
    if (!state.sessionId) return;
    if (!window.confirm(t('chat.delConfirm'))) return;
    try {
      await apiFetch('/v1/sessions/' + encodeURIComponent(state.sessionId), { method: 'DELETE' });
      state.sessionId = null;
      state.messages = [];
      await loadSessions();
      toast(t('ok.deleted'), 'success');
    } catch (e) { warn(e); }
  }

  function mdRender(src) {
    let text = esc(src);
    const blocks = [];
    const stash = (m, lang, code) => {
      blocks.push('<pre class="code-block" dir="ltr"><code>'
        + String(code).replace(/\n+$/, '') + '</code></pre>');
      return '\u0001' + (blocks.length - 1) + '\u0001';
    };
    // fenced code blocks (closed, then streaming-unclosed)
    text = text.replace(/```[A-Za-z0-9_+\-]*\r?\n?[\s\S]*?```/g, stash);
    text = text.replace(/```([A-Za-z0-9_+\-]*)\r?\n?([\s\S]*)$/g, stash);
    // inline code, bold
    text = text.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');
    text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/\n/g, '<br>');
    text = text.replace(/\u0001(\d+)\u0001/g, (m, i) => blocks[Number(i)] || '');
    return text;
  }

  function msgEl(m) {
    const div = document.createElement('div');
    if (m.role === 'notice') {
      div.className = 'msg msg-notice';
      div.innerHTML = '<div class="msg-content"></div>';
      div.querySelector('.msg-content').innerHTML = mdRender(m.content);
      return div;
    }
    div.className = 'msg ' + (m.role === 'user' ? 'msg-user' : 'msg-bot');
    const who = m.role === 'user' ? t('chat.you') : t('chat.assistant');
    div.innerHTML = '<div class="msg-head"><span class="msg-who">' + esc(who) + '</span>'
      + (m.stopped ? '<span class="msg-stopped">' + esc(t('chat.stopped')) + '</span>' : '')
      + '</div><div class="msg-content"></div>';
    const c = div.querySelector('.msg-content');
    c.innerHTML = m.role === 'user' ? esc(m.content).replace(/\n/g, '<br>') : mdRender(m.content);
    return div;
  }

  function renderMessages() {
    const box = $('#messages');
    box.innerHTML = '';
    if (!state.messages.length) {
      box.innerHTML = emptyStateHTML('chat', t('chat.empty'), t('chat.emptyHint'));
      return;
    }
    state.messages.forEach((m) => box.appendChild(msgEl(m)));
    scrollChat(true);
  }

  function scrollChat(force) {
    const box = $('#messages');
    const near = box.scrollHeight - box.scrollTop - box.clientHeight < 140;
    if (force || near) box.scrollTop = box.scrollHeight;
  }

  function setStreaming(on) {
    state.streaming = on;
    $('#sendBtn').hidden = on;
    $('#stopBtn').hidden = !on;
  }

  async function sendMessage() {
    const input = $('#chatInput');
    const text = input.value.trim();
    if (!text || state.streaming) return;
    if (!state.key) { openKeyModal(state.key); return; }
    // make sure a session exists (auto-create on first send)
    if (!state.sessionId || !state.sessions.some((s) => s.id === state.sessionId)) {
      try {
        const s = await apiFetch('/v1/sessions', {
          method: 'POST',
          body: { title: t('chat.newTitle'), mode: 'chat' }
        });
        state.sessions.unshift(s);
        state.sessionId = s.id;
        renderSessionSelect();
      } catch (e) { warn(e); return; }
    }
    state.messages.push({ role: 'user', content: text });
    const history = state.messages
      .filter((m) => (m.role === 'user' || m.role === 'assistant') && m.content)
      .slice(-30)
      .map((m) => ({ role: m.role, content: m.content }));
    const bot = { role: 'assistant', content: '' };
    state.messages.push(bot);
    input.value = '';
    autoGrow(input);
    renderMessages();
    setStreaming(true);
    const ctrl = new AbortController();
    state.abort = ctrl;
    try {
      const res = await fetch(state.origin + '/v1/chat/completions', {
        method: 'POST',
        signal: ctrl.signal,
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          model: null,
          messages: history,
          max_tokens: 1024,
          temperature: 0.7,
          stream: true,
          session_id: state.sessionId
        })
      });
      if (!res.ok) {
        // e.g. 503 with Persian "model not ready" detail -> system notice in chat
        bot.role = 'notice';
        bot.content = await parseErr(res);
        return;
      }
      const box = $('#messages');
      const live = msgEl(bot);
      box.appendChild(live);
      scrollChat(true);
      const contentEl = live.querySelector('.msg-content');
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      let done = false;
      while (!done) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buf += dec.decode(chunk.value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (let i = 0; i < lines.length; i++) {
          const line = lines[i].trim();
          if (line.indexOf('data:') !== 0) continue;
          const payload = line.slice(5).trim();
          if (payload === '[DONE]') { done = true; break; }
          try {
            const d = JSON.parse(payload);
            const delta = d && d.choices && d.choices[0] && d.choices[0].delta && d.choices[0].delta.content;
            if (delta) {
              bot.content += delta;
              contentEl.innerHTML = mdRender(bot.content);
              scrollChat(false);
            }
          } catch (e) { /* partial json, ignore */ }
        }
      }
    } catch (e) {
      if (e && e.name === 'AbortError') {
        bot.stopped = true;
      } else {
        bot.role = 'notice';
        bot.content = (e && e.message) || t('err.network');
      }
    } finally {
      setStreaming(false);
      state.abort = null;
      renderMessages();
    }
  }

  function stopChat() {
    if (state.abort) state.abort.abort();
  }

  function autoGrow(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  }

  /* ==========================================================================
     AGENT
     ========================================================================== */

  function statusBadge(st) {
    return '<span class="badge st-' + esc(st) + '">' + esc(t('status.' + st)) + '</span>';
  }

  function stepIcon(st) {
    if (st === 'ok') return 'check';
    if (st === 'error') return 'close';
    if (st === 'running') return 'refresh';
    if (st === 'awaiting_approval') return 'alert';
    return 'clock';
  }

  function phaseLabel(phase) {
    const k = 'phase.' + phase;
    const v = t(k);
    return v === k ? phase : v;
  }

  async function loadTasks() {
    try {
      const j = await apiFetch('/v1/agent/tasks');
      state.tasks = (j && j.data) || [];
    } catch (e) {
      state.tasks = [];
      warn(e);
    }
    renderTaskList();
    if (state.selectedTask) await loadTaskDetail(state.selectedTask, true);
    scheduleAgentPoll();
  }

  function renderTaskList() {
    const box = $('#taskList');
    box.innerHTML = '';
    if (!state.tasks.length) {
      box.innerHTML = emptyStateHTML('agent', t('agent.noTasks'), '');
      return;
    }
    state.tasks.forEach((tk) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'task-row' + (tk.id === state.selectedTask ? ' active' : '');
      row.innerHTML =
        '<div class="task-row-top">' + statusBadge(tk.status)
        + '<span class="small muted mono" dir="ltr">' + esc(String(tk.id || '').slice(0, 8)) + '</span></div>'
        + '<p class="task-instr">' + esc(tk.instruction || '') + '</p>'
        + '<div class="task-row-meta small muted"><span>' + esc(t('agent.steps')) + ': '
        + esc(tk.steps_count != null ? tk.steps_count : 0) + '</span><span>'
        + esc(t('mode.' + (tk.mode || 'auto'))) + '</span></div>';
      row.addEventListener('click', () => selectTask(tk.id));
      box.appendChild(row);
    });
  }

  function selectTask(id) {
    state.selectedTask = id;
    state.openSteps = {};
    renderTaskList();
    loadTaskDetail(id, false);
    if (window.innerWidth < 760) {
      const d = $('#taskDetail');
      if (d && d.scrollIntoView) d.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  async function loadTaskDetail(id, silent) {
    try {
      state.taskDetail = await apiFetch('/v1/agent/tasks/' + encodeURIComponent(id));
      renderTaskDetail();
    } catch (e) {
      if (!silent) warn(e);
    }
  }

  function parseResultSummary(d) {
    if (!d || d.result == null) return '';
    if (typeof d.result === 'object') return d.result.summary || '';
    try {
      const r = JSON.parse(d.result);
      return (r && r.summary) || '';
    } catch (e) { return ''; }
  }

  function renderTaskDetail() {
    const box = $('#taskDetail');
    const d = state.taskDetail;
    if (!d) {
      box.innerHTML = emptyStateHTML('agent', t('agent.noDetail'), '');
      return;
    }
    let h = '';
    h += '<div class="detail-head">' + statusBadge(d.status)
      + '<span class="small muted mono" dir="ltr">' + esc(d.id) + '</span>'
      + '<span class="chip">' + esc(t('mode.' + (d.mode || 'auto'))) + '</span>'
      + '<span class="chip">' + esc(t('agent.cycles')) + ': ' + esc(d.cycles != null ? d.cycles : 0) + '</span>'
      + '</div>';
    h += '<p class="task-instruction-detail">' + esc(d.instruction || '') + '</p>';

    if (d.status === 'awaiting_approval') {
      h += '<div class="approve-panel">'
        + '<div class="approve-title">' + icon('alert') + '<span>' + esc(t('agent.approvalTitle')) + '</span></div>'
        + '<p class="small muted">' + esc(t('agent.approvalBody')) + '</p>'
        + '<div class="row wrap">'
        + '<button type="button" class="btn btn-green" id="approveBtn">' + esc(t('agent.approve')) + '</button>'
        + '<button type="button" class="btn btn-danger-ghost" id="denyBtn">' + esc(t('agent.deny')) + '</button>'
        + '</div></div>';
    }

    const summary = parseResultSummary(d);
    if (summary) {
      h += '<div class="summary-panel"><div class="panel-label">' + esc(t('agent.summary')) + '</div>'
        + '<p>' + mdRender(summary) + '</p></div>';
    }
    if (d.error) {
      h += '<div class="error-panel"><div class="panel-label">' + esc(t('agent.errorLabel')) + '</div>'
        + '<p>' + esc(d.error) + '</p></div>';
    }

    const steps = Array.isArray(d.steps) ? d.steps : [];
    if (steps.length) {
      const groups = [];
      steps.forEach((s) => {
        let g = groups.find((x) => x.phase === s.phase);
        if (!g) { g = { phase: s.phase, steps: [] }; groups.push(g); }
        g.steps.push(s);
      });
      h += '<div class="panel-label">' + esc(t('agent.steps')) + ' (' + steps.length + ')</div>';
      groups.forEach((g) => {
        h += '<div class="phase"><div class="phase-head">' + esc(phaseLabel(g.phase)) + ' (' + g.steps.length + ')</div>';
        g.steps.forEach((s) => {
          const open = !!state.openSteps[s.id];
          h += '<div class="step' + (open ? ' open' : '') + '" data-step="' + esc(s.id) + '">'
            + '<button type="button" class="step-head">'
            + '<span class="step-ic st-' + esc(s.status || 'pending') + '" title="' + esc(t('step.' + (s.status || 'pending'))) + '">'
            + icon(stepIcon(s.status)) + '</span>'
            + '<span class="step-title">' + esc(s.title || '') + '</span>'
            + '<code class="step-tool mono" dir="ltr">' + esc(s.tool || '') + '</code>'
            + '<span class="chev">' + icon('chevronDown') + '</span>'
            + '</button>'
            + '<div class="step-body"' + (open ? '' : ' hidden') + '>'
            + '<div class="step-out-head"><span class="small muted">' + esc(t('agent.output')) + '</span>'
            + '<button type="button" class="btn btn-ghost btn-xs copy-out">' + icon('copy')
            + '<span>' + esc(t('common.copy')) + '</span></button></div>'
            + '<pre class="code-block" dir="ltr">' + esc(s.output || t('agent.noOutput')) + '</pre>'
            + '</div></div>';
        });
        h += '</div>';
      });
    }
    box.innerHTML = h;

    const ab = box.querySelector('#approveBtn');
    if (ab) ab.addEventListener('click', () => approveTask(true));
    const db = box.querySelector('#denyBtn');
    if (db) db.addEventListener('click', () => approveTask(false));
  }

  async function createTask() {
    const ta = $('#agentInstruction');
    const text = ta.value.trim();
    if (text.length < 2) { toast(t('err.fill'), 'error'); return; }
    const btn = $('#agentRunBtn');
    btn.disabled = true;
    try {
      const task = await apiFetch('/v1/agent/tasks', {
        method: 'POST',
        body: { instruction: text, mode: $('#agentMode').value }
      });
      ta.value = '';
      toast(t('ok.taskCreated'), 'success');
      state.selectedTask = task.id;
      state.openSteps = {};
      await loadTasks();
      await loadTaskDetail(task.id, true);
    } catch (e) {
      warn(e);
    } finally {
      btn.disabled = false;
    }
  }

  async function approveTask(approved) {
    const id = state.selectedTask;
    if (!id) return;
    try {
      await apiFetch('/v1/agent/tasks/' + encodeURIComponent(id) + '/approve', {
        method: 'POST',
        body: { approved: !!approved }
      });
      toast(approved ? t('ok.approved') : t('ok.denied'), 'success');
      await loadTaskDetail(id, true);
      apiFetch('/v1/agent/tasks').then((j) => {
        state.tasks = (j && j.data) || [];
        renderTaskList();
        scheduleAgentPoll();
      }).catch(() => { /* ignore */ });
    } catch (e) { warn(e); }
  }

  function scheduleAgentPoll() {
    const anyActive = state.tasks.some((x) => ACTIVE_STATUSES.indexOf(x.status) !== -1);
    if (anyActive && state.view === 'agent' && !state.agentTimer) {
      state.agentTimer = setInterval(async () => {
        if (state.view !== 'agent') { stopAgentPoll(); return; }
        try {
          const j = await apiFetch('/v1/agent/tasks');
          state.tasks = (j && j.data) || [];
          renderTaskList();
          if (state.selectedTask) await loadTaskDetail(state.selectedTask, true);
          if (!state.tasks.some((x) => ACTIVE_STATUSES.indexOf(x.status) !== -1)) stopAgentPoll();
        } catch (e) { /* keep polling silently */ }
      }, 2500);
    }
  }

  function stopAgentPoll() {
    if (state.agentTimer) { clearInterval(state.agentTimer); state.agentTimer = null; }
  }

  /* ==========================================================================
     FILES
     ========================================================================== */

  async function loadDir(path) {
    state.filesPath = path || '.';
    try {
      const j = await apiFetch('/v1/files?path=' + encodeURIComponent(state.filesPath));
      state.fileItems = ((j && j.items) || []).filter((it) => it.name !== '.keep');
    } catch (e) {
      state.fileItems = [];
      warn(e);
    }
    renderCrumbs();
    renderFileList();
  }

  function renderCrumbs() {
    const bar = $('#crumbBar');
    bar.innerHTML = '';
    const mk = (label, path) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'crumb';
      b.textContent = label;
      b.addEventListener('click', () => loadDir(path));
      bar.appendChild(b);
    };
    mk(t('files.root'), '.');
    if (state.filesPath && state.filesPath !== '.') {
      const parts = state.filesPath.split('/');
      let acc = '';
      parts.forEach((p) => {
        acc = acc ? acc + '/' + p : p;
        const sep = document.createElement('span');
        sep.className = 'crumb-sep';
        sep.innerHTML = icon('chevron');
        bar.appendChild(sep);
        mk(p, acc);
      });
    }
  }

  function renderFileList() {
    const list = $('#fileList');
    list.innerHTML = '';
    if (!state.fileItems.length) {
      list.innerHTML = emptyStateHTML('folder', t('files.empty'), '');
      return;
    }
    state.fileItems.forEach((it) => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'file-row' + (it.type === 'dir' ? ' is-dir' : '');
      if (it.mtime) row.title = new Date(it.mtime * 1000).toLocaleString();
      row.innerHTML = '<span class="file-ic">' + icon(it.type === 'dir' ? 'folder' : 'file') + '</span>'
        + '<span class="file-name">' + esc(it.name) + '</span>'
        + '<span class="file-size small muted">' + (it.type === 'dir' ? '' : fmtBytes(it.size)) + '</span>';
      row.addEventListener('click', () => {
        if (it.type === 'dir') loadDir(it.path);
        else openEditor(it.path);
      });
      list.appendChild(row);
    });
  }

  async function openEditor(path) {
    try {
      const j = await apiFetch('/v1/files/content?path=' + encodeURIComponent(path));
      state.editorPath = path;
      $('#editorTitle').textContent = String(path).split('/').pop();
      $('#editorPath').textContent = (j && j.path) || path;
      $('#editorArea').value = (j && j.content) || '';
      $('#editorPanel').hidden = false;
      $('#filesGrid').classList.add('editor-open');
    } catch (e) { warn(e); }
  }

  function closeEditor() {
    state.editorPath = null;
    $('#editorPanel').hidden = true;
    $('#filesGrid').classList.remove('editor-open');
  }

  async function saveFile() {
    if (!state.editorPath) return;
    try {
      await apiFetch('/v1/files/content', {
        method: 'PUT',
        body: { path: state.editorPath, content: $('#editorArea').value }
      });
      toast(t('ok.saved'), 'success');
      loadDir(state.filesPath);
    } catch (e) { warn(e); }
  }

  async function deleteFile() {
    if (!state.editorPath) return;
    if (!window.confirm(t('files.confirmDelete') + '\n' + state.editorPath)) return;
    try {
      await apiFetch('/v1/files?path=' + encodeURIComponent(state.editorPath), { method: 'DELETE' });
      toast(t('ok.deleted'), 'success');
      closeEditor();
      loadDir(state.filesPath);
    } catch (e) { warn(e); }
  }

  async function newEntry(kind) {
    const name = window.prompt(t('files.namePrompt'));
    if (name == null) return;
    const clean = name.trim().replace(/^\/+|\/+$/g, '');
    if (!clean) { toast(t('err.fill'), 'error'); return; }
    const base = state.filesPath === '.' ? '' : state.filesPath + '/';
    // folders are created by writing a marker file inside (backend mkdir -p the parent)
    const target = kind === 'dir' ? base + clean + '/.keep' : base + clean;
    try {
      await apiFetch('/v1/files/content', { method: 'PUT', body: { path: target, content: '' } });
      toast(t('ok.created'), 'success');
      await loadDir(state.filesPath);
      if (kind === 'file') openEditor(base + clean);
    } catch (e) { warn(e); }
  }

  /* ==========================================================================
     PROJECTS
     ========================================================================== */

  async function loadProjects() {
    try {
      const j = await apiFetch('/v1/projects');
      state.projects = (j && j.data) || [];
    } catch (e) {
      state.projects = [];
      warn(e);
    }
    renderProjects();
  }

  function exportUrl(id) {
    return state.origin + '/v1/projects/' + encodeURIComponent(id)
      + '/export?api_key=' + encodeURIComponent(state.key || '');
  }

  function renderProjects() {
    const g = $('#projectsGrid');
    g.innerHTML = '';
    if (!state.projects.length) {
      g.innerHTML = emptyStateHTML('projects', t('projects.empty'), t('projects.emptyHint'));
      return;
    }
    state.projects.forEach((p) => {
      const card = document.createElement('div');
      card.className = 'panel project-card';
      card.innerHTML =
        '<div class="card-head"><strong class="proj-name">' + esc(p.name) + '</strong>'
        + '<span class="badge b-accent">' + esc(t('tpl.' + (p.template || 'python'))) + '</span></div>'
        + '<div class="proj-meta small muted"><span>' + esc(t('projects.files')) + ': '
        + esc(p.files_count != null ? p.files_count : 0) + '</span></div>'
        + '<div class="card-actions">'
        + '<button type="button" class="btn btn-ghost btn-sm act-run">' + icon('system')
        + '<span>' + esc(t('projects.run')) + '</span></button>'
        + '<button type="button" class="btn btn-ghost btn-sm act-test">' + icon('check')
        + '<span>' + esc(t('projects.test')) + '</span></button>'
        + '<a class="btn btn-ghost btn-sm" href="' + esc(exportUrl(p.id)) + '" download>'
        + '<svg class="ic" viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3.5V13M6.5 9.5l3.5 3.5 3.5-3.5M4 16.5h12"/></svg>'
        + '<span>' + esc(t('projects.export')) + '</span></a>'
        + '</div>';
      card.querySelector('.act-run').addEventListener('click', () => projectAction(p, 'run'));
      card.querySelector('.act-test').addEventListener('click', () => projectAction(p, 'test'));
      g.appendChild(card);
    });
  }

  async function projectAction(p, kind) {
    try {
      const j = await apiFetch('/v1/projects/' + encodeURIComponent(p.id) + '/' + kind, { method: 'POST' });
      const title = (kind === 'run' ? t('projects.run') : t('projects.test')) + ': ' + p.name;
      openOutputModal(title, j.output || '', j.exit_code);
    } catch (e) { warn(e); }
  }

  function openOutputModal(title, output, exit) {
    let badge = '';
    if (exit != null) {
      badge = '<span class="badge ' + (Number(exit) === 0 ? 'b-green' : 'b-red') + '">'
        + esc(t('projects.exitCode')) + ': ' + esc(String(exit)) + '</span>';
    }
    const m = modalShell(title,
      (badge ? '<div class="row" style="margin-bottom:8px">' + badge + '</div>' : '')
      + '<pre class="code-block" dir="ltr">' + (output ? esc(output) : esc(t('common.none'))) + '</pre>'
      + '<div class="modal-actions"><button type="button" class="btn btn-ghost btn-sm" id="outCopy">'
      + icon('copy') + '<span>' + esc(t('common.copy')) + '</span></button></div>');
    m.ov.querySelector('#outCopy').addEventListener('click', () => copyText(output || ''));
  }

  function openProjectModal() {
    const m = modalShell(t('projects.new'),
      '<label class="field"><span>' + esc(t('projects.name')) + '</span>'
      + '<input id="projName" class="input" dir="ltr" autocomplete="off"></label>'
      + '<label class="field"><span>' + esc(t('projects.template')) + '</span>'
      + '<select id="projTpl" class="select">'
      + '<option value="python">' + esc(t('tpl.python')) + '</option>'
      + '<option value="node">' + esc(t('tpl.node')) + '</option>'
      + '</select></label>'
      + '<div class="modal-actions">'
      + '<button type="button" class="btn btn-primary" id="projCreate">' + esc(t('projects.create')) + '</button>'
      + '</div>');
    m.ov.querySelector('#projCreate').addEventListener('click', async () => {
      const name = m.ov.querySelector('#projName').value.trim();
      if (!name) { toast(t('err.fill'), 'error'); return; }
      try {
        const j = await apiFetch('/v1/projects', {
          method: 'POST',
          body: { name, template: m.ov.querySelector('#projTpl').value }
        });
        m.close();
        toast((j && j.message) || t('ok.created'), 'success');
        loadProjects();
      } catch (e) { warn(e); }
    });
    setTimeout(() => { const i = m.ov.querySelector('#projName'); if (i) i.focus(); }, 60);
  }

  /* ==========================================================================
     SYSTEM
     ========================================================================== */

  function startSystem() {
    fetchSystem();
    stopSystem();
    state.systemTimer = setInterval(() => {
      if (state.view !== 'system') { stopSystem(); return; }
      fetchSystem();
    }, 5000);
  }

  function stopSystem() {
    if (state.systemTimer) { clearInterval(state.systemTimer); state.systemTimer = null; }
  }

  async function fetchSystem() {
    try {
      state.systemData = await apiFetch('/v1/system/status');
      renderSystem();
    } catch (e) {
      if (e.status !== 401) toast(e.message || t('err.network'), 'error');
    }
  }

  function kv(label, value) {
    return '<div class="kv"><span>' + esc(label) + '</span><b>' + value + '</b></div>';
  }

  function barHtml(pct, txt) {
    return '<div class="bar"><i class="bar-fill ' + barCls(pct) + '" style="width:'
      + clampPct(pct) + '%"></i></div>'
      + '<div class="small muted bar-txt">' + txt + '</div>';
  }

  function sysCard(title, body) {
    return '<div class="panel sys-card"><h4>' + esc(title) + '</h4>' + body + '</div>';
  }

  function renderSystem() {
    const g = $('#sysGrid');
    const d = state.systemData;
    if (!d) {
      g.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
      return;
    }
    const ram = d.ram || {};
    const disk = d.disk || {};
    const model = d.model || {};
    const llm = d.llm || {};
    const agent = d.agent || {};
    const plat = d.platform || {};
    const cs = d.codespace || {};
    const load = Array.isArray(d.load_avg) ? d.load_avg : [];

    let h = '';
    h += sysCard(t('system.ram'), barHtml(ram.percent,
      esc((ram.used_mb != null ? ram.used_mb : 0) + ' / ' + (ram.total_mb != null ? ram.total_mb : 0)
        + ' MB - ' + (ram.percent != null ? ram.percent : 0) + '%')));
    h += sysCard(t('system.cpu'), barHtml(d.cpu_percent,
      esc((d.cpu_percent != null ? d.cpu_percent : 0) + '%')
      + ' | ' + esc(t('system.load')) + ': ' + esc(load.length ? load.join(' / ') : t('common.none'))));
    h += sysCard(t('system.disk'), barHtml(disk.percent,
      esc((disk.used_gb != null ? disk.used_gb : 0) + ' / ' + (disk.total_gb != null ? disk.total_gb : 0)
        + ' GB - ' + (disk.percent != null ? disk.percent : 0) + '%')));

    const modelBody =
      kv(t('system.modelId'), '<span class="mono" dir="ltr">' + esc(model.id || t('common.none')) + '</span>')
      + kv(t('system.status'), model.loaded
        ? '<span class="badge b-green">' + esc(t('system.loaded')) + '</span>'
        : '<span class="badge b-yellow">' + esc(t('system.notLoaded')) + '</span>')
      + kv(t('system.size'), esc(fmtBytes(model.size_mb != null ? model.size_mb * 1048576 : null)))
      + kv(t('system.rss'), esc(fmtBytes(model.llama_rss_mb != null ? model.llama_rss_mb * 1048576 : null)))
      + kv(t('system.ctx'), esc(model.ctx != null ? model.ctx : t('common.none')))
      + kv(t('system.threads'), esc(model.threads != null ? model.threads : t('common.none')))
      + kv(t('system.tps'), esc((llm.last_tok_s != null ? llm.last_tok_s : t('common.none'))
        + ' ' + t('system.tps')));
    h += sysCard(t('system.model'), modelBody);

    const agentBody =
      kv(t('system.activeTasks'), esc(agent.active_tasks != null ? agent.active_tasks : 0))
      + kv(t('system.worker'), agent.worker_alive
        ? '<span class="badge b-green">' + esc(t('system.on')) + '</span>'
        : '<span class="badge b-muted">' + esc(t('system.off')) + '</span>');
    h += sysCard(t('system.agent'), agentBody);

    const platBody =
      kv(t('system.os'), esc(plat.os || t('common.none')))
      + kv(t('system.cpus'), esc(plat.cpus != null ? plat.cpus : t('common.none')))
      + kv(t('system.python'), esc(plat.python || t('common.none')))
      + kv(t('system.version'), esc(d.version || t('common.none')))
      + kv(t('system.uptime'), '<span class="uptime-big" style="font-size:14px">'
        + esc(fmtUptime(d.uptime_s)) + '</span>');
    h += sysCard(t('system.platform'), platBody);

    if (cs.name) {
      h += sysCard(t('system.codespace'),
        kv(t('system.codespace'), '<span class="mono" dir="ltr">' + esc(cs.name) + '</span>')
        + kv(t('system.region'), esc(cs.region || t('common.none'))));
    }
    g.innerHTML = h;
  }

  /* ==========================================================================
     SETTINGS
     ========================================================================== */

  function pySnippetText() {
    return [
      'import requests',
      '',
      'BASE_URL = "' + state.origin + '"',
      'API_KEY  = "' + (state.key || '<API_KEY>') + '"',
      '',
      'resp = requests.post(',
      '    BASE_URL + "/v1/chat/completions",',
      '    headers={"Authorization": "Bearer " + API_KEY},',
      '    json={"messages": [{"role": "user", "content": "Salam"}], "stream": False},',
      '    timeout=120,',
      ')',
      'resp.raise_for_status()',
      'print(resp.json()["choices"][0]["message"]["content"])'
    ].join('\n');
  }

  function jsSnippetText() {
    return [
      'const BASE_URL = "' + state.origin + '";',
      'const API_KEY = "' + (state.key || '<API_KEY>') + '";',
      '',
      'const res = await fetch(BASE_URL + "/v1/chat/completions", {',
      '  method: "POST",',
      '  headers: { "Authorization": "Bearer " + API_KEY, "Content-Type": "application/json" },',
      '  body: JSON.stringify({ messages: [{ role: "user", content: "Salam" }] }),',
      '});',
      'const data = await res.json();',
      'console.log(data.choices[0].message.content);'
    ].join('\n');
  }

  function renderSettings() {
    const k = state.key || '';
    $('#keyMasked').textContent = k
      ? (k.slice(0, 4) + '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' + k.slice(-4))
      : t('common.none');
    $('#pySnippet').textContent = pySnippetText();
    $('#jsSnippet').textContent = jsSnippetText();
    updateLangUI();
  }

  async function rotateKey() {
    if (!window.confirm(t('settings.rotateConfirm'))) return;
    try {
      const j = await apiFetch('/api/key/rotate', { method: 'POST' });
      if (j && j.key) {
        state.key = j.key;
        ls.set('deldelak_key', j.key);
        toast(t('ok.rotated'), 'success');
        renderSettings();
      }
    } catch (e) { warn(e); }
  }

  /* ==========================================================================
     STATIC WIRING + BOOT
     ========================================================================== */

  function bindChrome() {
    $$('.nav-btn').forEach((b) => {
      b.addEventListener('click', () => showView(b.getAttribute('data-view')));
    });
    $('#langToggle').addEventListener('click', () => setLang(state.lang === 'fa' ? 'en' : 'fa'));
    $('#langFa').addEventListener('click', () => setLang('fa'));
    $('#langEn').addEventListener('click', () => setLang('en'));

    // chat
    $('#sessionSelect').addEventListener('change', (e) => {
      state.sessionId = e.target.value || null;
      if (state.sessionId) loadSessionMessages(state.sessionId);
    });
    $('#newSessionBtn').addEventListener('click', newSession);
    $('#delSessionBtn').addEventListener('click', deleteSession);
    $('#sendBtn').addEventListener('click', sendMessage);
    $('#stopBtn').addEventListener('click', stopChat);
    const chatInput = $('#chatInput');
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    chatInput.addEventListener('input', () => autoGrow(chatInput));

    // agent
    $('#agentRunBtn').addEventListener('click', createTask);
    $('#agentRefreshBtn').addEventListener('click', loadTasks);
    $('#taskDetail').addEventListener('click', (e) => {
      const copyBtn = e.target.closest('.copy-out');
      if (copyBtn) {
        const body = copyBtn.closest('.step-body');
        const pre = body ? body.querySelector('pre') : null;
        copyText(pre ? pre.textContent : '');
        return;
      }
      if (e.target.closest('#approveBtn') || e.target.closest('#denyBtn')) return; // bound at render
      const head = e.target.closest('.step-head');
      if (head) {
        const step = head.parentElement;
        const body = step.querySelector('.step-body');
        const id = step.getAttribute('data-step');
        const willOpen = body.hidden;
        if (willOpen) { body.hidden = false; step.classList.add('open'); state.openSteps[id] = true; }
        else { body.hidden = true; step.classList.remove('open'); delete state.openSteps[id]; }
      }
    });

    // files
    $('#newFileBtn').addEventListener('click', () => newEntry('file'));
    $('#newFolderBtn').addEventListener('click', () => newEntry('dir'));
    $('#refreshFilesBtn').addEventListener('click', () => loadDir(state.filesPath));
    $('#saveFileBtn').addEventListener('click', saveFile);
    $('#deleteFileBtn').addEventListener('click', deleteFile);
    $('#editorCloseBtn').addEventListener('click', closeEditor);
    $('#copyPathBtn').addEventListener('click', () => copyText(state.editorPath || state.filesPath));

    // projects
    $('#newProjectBtn').addEventListener('click', openProjectModal);

    // system
    $('#sysRefreshBtn').addEventListener('click', fetchSystem);

    // settings
    $('#copyKeyBtn').addEventListener('click', () => copyText(state.key));
    $('#rotateKeyBtn').addEventListener('click', rotateKey);
    $('#copyPyBtn').addEventListener('click', () => copyText(pySnippetText()));
    $('#copyJsBtn').addEventListener('click', () => copyText(jsSnippetText()));
  }

  async function boot() {
    applyStaticI18n();
    bindChrome();
    startHealthPoll();
    try {
      const ok = await initKey();
      if (ok) afterAuth();
      else showView('chat'); // key modal is open; UI stays interactive
    } catch (e) {
      showView('chat');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
