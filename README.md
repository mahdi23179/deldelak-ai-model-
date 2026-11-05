<div dir="rtl" align="center">

# دلدلک | Deldelak AI Model

**عامل هوش مصنوعی کاملاً محلی (فارسی/انگلیسی) روی GitHub Codespaces — بدون هیچ API ابری**

مدل زبانی + موتور عامل (برنامه‌ریز → مجری → تأییدکننده) + وب‌چت فارسی، همه در یک Codespace که با یک کلیک روشن می‌شود.

[![CI](https://github.com/mahdi23179/deldelak-ai-model-/actions/workflows/ci.yml/badge.svg)](https://github.com/mahdi23179/deldelak-ai-model-/actions/workflows/ci.yml)
[![Wake Bot](https://img.shields.io/badge/Wake-Bot_بیدار_کن-1F6FEB)](https://github.com/mahdi23179/deldelak-ai-model-/actions/workflows/wake-codespace.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-238636)](LICENSE)

</div>

---

<div dir="rtl">

## این چیست؟

دلدلک یک هوش مصنوعی **کاملاً محلی** است:

- **مدل:** Qwen2.5-3B-Instruct (کوانتیزه Q4_K_M، حجم فایل ≈ ۲ گیگابایت) با llama.cpp روی CPU — بدون GPU، بدون OpenAI، بدون اینترنت پس از نصب.
- **موتور عامل:** برنامه‌ریز (Planner) → مجری (Executor) → تأییدکننده (Verifier) با ۱۵ ابزار واقعی: اجرای دستور ترمینال، اجرای پایتون، خواندن/نوشتن/ویرایش/حذف فایل، ساخت/اجرای/تست/خروجی‌گرفتن پروژه و git.
- **وب‌چت فارسی:** رابط کاربری RTL با تم تیره گیت‌هاب، استریم پاسخ، مدیریت فایل و پروژه، مانیتورینگ زنده CPU/RAM.
- **API سازگار با OpenAI:** هر کلاینتی که با OpenAI کار می‌کند، با دلدلک هم کار می‌کند.

## شروع سریع (۳ قدم، بدون نصب هیچ‌چیز)

1. روی دکمه **Code → Codespaces → Create codespace on main** کلیک کنید.
2. صبر کنید تا نصب خودکار تمام شود (بار اول ≈ ۱۰ تا ۲۰ دقیقه: ساخت llama.cpp + بارگیری ۲GB مدل با قابلیت ادامه از محل قطع).
3. تب مرورگر UI چت باز می‌شود. مالک با «ورود با گیت‌هاب» کلید را بازیابی می‌کند (`/api/key/oauth/start`) — تمام!

## روشن‌شدن خودکار (Auto-Wake v2) — بدون هیچ اقدام دستی

کداسپیس‌ها بعد از خلوتی خاموش می‌شوند. راه‌حل جدید یک **Gateway همیشه‌در‌دسترس** است:

```
کاربر → https://<gateway>.workers.dev/v1/chat/completions (فقط یک URL و یک کلید)
          ├─ Codespace روشن است → پروکسی مستقیم
          └─ Codespace خاموش است → روشن‌سازی رسمی با Codespaces API → درخواست در صف محفوظ → پس از آماده‌شدن اجرا
                                        └─ پس از ۱۰ دقیقه بی‌کاری: خاموشی خودکار (idempotent)
```

- دیگر نیازی به `!wake`، ساخت Issue، ورود به GitHub یا اجرای workflow نیست — همه حذف شدند.
- پاسخ ساختگی وجود ندارد: اگر Cold Start طولانی شد، 503 + Retry-After + شناسهٔ درخواست برمی‌گردد و درخواست گم نمی‌شود.
- Keep-Alive دائمی ممنوع است و از طراحی حذف شده (سهمیهٔ رایگان حفظ می‌شود).

راه‌اندازی یک‌باره: [`docs/GATEWAY.md`](docs/GATEWAY.md) • اپراتوری دستی مالک: workflow «Codespace ops»

جزئیات: [`docs/GATEWAY.md`](docs/GATEWAY.md) • محدودیت‌ها: [`docs/LIMITS.md`](docs/LIMITS.md)

## API

| مسیر | توضیح |
|---|---|
| `GET /health` | سلامت سه‌لایه: گیت‌وی/بک‌اند/مدل + صف و فعالیت (بدون احراز) |
| `GET /v1/models` | فهرست مدل |
| `POST /v1/chat/completions` | چت سازگار با OpenAI (پشتیبانی استریم) |
| `GET /api/key` | فقط راهنمای بازیابی مالک — کلید به غریبه داده نمی‌شود |
| `GET /api/key/oauth/start` | ورود مالک با گیت‌هاب (فقط حساب `mahdi23179`) |
| `POST /api/key/reveal` · `/rotate` · `/revoke` | نمایش/چرخش/ابطال کلید (نشست مالک یا کلید معتبر + CSRF) |
| `POST /v1/projects/{id}/export-token` | توکن یک‌بارمصرف دانلود (کلید هرگز در URL نیست) |
| `GET /v1/requests/{id}` (روی گیت‌وی) | وضعیت درخواست‌های صف‌شده هنگام Cold Start |
| `POST /v1/agent/tasks` | ثبت تسک برای عامل |
| `GET /v1/system/status` | CPU/RAM/دیسک/مدل/توکن‌برثانیه |

احراز هویت: هدر `Authorization: Bearer <key>` یا `X-API-Key: <key>`.
مستند کامل: [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md).

نمونه پایتون:

```python
import requests
BASE = "https://<your-codespace>-8000.app.github.dev"
KEY  = "ddk-..."   # از GET /api/key یا فایل .state/api_key
r = requests.post(f"{BASE}/v1/chat/completions",
    headers={"Authorization": f"Bearer {KEY}"},
    json={"messages": [{"role": "user", "content": "سلام! خودت رو معرفی کن"}]})
print(r.json()["choices"][0]["message"]["content"])
```

## معماری

```
مرورگر کاربر (فارسی RTL)
        │  فقط پورت 8000 فوروارد می‌شود
        ▼
FastAPI :8000  ── احراز هویت کلید + استریم + مدیریت فایل/پروژه
   │                        │
   │                        └── AgentEngine (نخ جداگانه)
   │                              Planner → Executor(15 ابزار) → Verifier
   │                              SQLite: تسک‌ها، گام‌ها، چک‌پوینت‌ها، خطاها
   ▼
llama-server :8081 (فقط 127.0.0.1 — هرگز بیرون نیست)
   └── Qwen2.5-3B-Instruct Q4_K_M روی CPU
```

- سرور مدل فقط روی `127.0.0.1` گوش می‌دهد؛ تنها چیزی که از Codespace بیرون می‌زند API پورت 8000 است.
- مدل در گیت ذخیره **نمی‌شود**؛ `setup.sh` در اولین راه‌اندازی آن را با `curl -C -` (ادامه از محل قطع) می‌گیرد و **sha256** را با متادیتای HuggingFace تأیید می‌کند.
- همه وضعیت‌ها (گفتگوها، تسک‌ها، چک‌پوینت‌ها، نتایج تست) در SQLite ماندگارند و پس از ری‌استارت برمی‌گردند.
- عملیات حساس (حذف فایل، دستورهای خطرناک) متوقف می‌شوند و از UI تأیید می‌گیرند.

## محدودیت‌ها (صادقانه)

- **Codespaces رایگان:** ۱۲۰ ساعت-هسته در ماه → با ۲ هسته یعنی **حدود ۶۰ ساعت اجرا در ماه** + ۱۵ گیگابایت-ماه ذخیره‌سازی. نگه‌داشتن دائمی ۲۴/۷ با پلن رایگان ممکن نیست.
- **خاموشی خودکار:** پیش‌فرض ۳۰ دقیقه خلوتی (حداکثر قابل افزایش به ۲۴۰ دقیقه). راه‌حل: Auto-Wake بالا.
- **سرعت CPU:** انتظار حدود ۵ تا ۱۰ توکن بر ثانیه برای مدل ۳B روی ۲ هسته؛ اعداد واقعی اندازه‌گیری‌شده در [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md).
- دور اولِ پاسخ کمی کندتر است (بارگیری مدل در RAM).
- این پروژه هیچ سهمیه‌ای را دور نمی‌زند و از همان محدودیت‌های رسمی GitHub استفاده می‌کند.

## توسعه محلی (بدون مدل)

```bash
pip install -r requirements.txt -r requirements-dev.txt
python3 -m pytest tests            # ۲۲ تست — با مدل ماک، بدون دانلود
python3 -m uvicorn backend.main:app --port 8000   # UI روی http://localhost:8000
```

تا وقتی `llama-server` و مدل نصب نشده باشند، سرویس با وضعیت `degraded` بالا می‌آید، UI کار می‌کند و مسیر چت پیام شفاف فارسی برمی‌گرداند.

## تست‌ها

- `tests/` — ۲۲ تست خودکار (API، استریم، عامل، ابزارها، پروژه‌ها، ماندگاری، امنیت مسیر).
- `tests/run_all.sh` — اجرای کامل + گزارش فارسی در `.state/test_report.txt`.
- `tests/real_agent_tasks.sh` — ۵ تسک واقعی عامل روی سرور زنده (برای تست درون Codespace).
- CI روی هر push اجرا می‌شود.

## ساختار

```
.devcontainer/     نصب خودکار Codespace (setup + start)
.github/workflows/ ci.yml + codespace-ops.yml (عملیات دستی مالک با تأیید واقعی وضعیت)
backend/           FastAPI، llama.cpp، SQLite، AgentEngine، ابزارهای سخت‌گیرانه
frontend/          UI فارسی RTL (HTML/CSS/JS خالص) + وضعیت سرویس + مدیریت کلید مالک
gateway/           Cloudflare Worker + Durable Object (Wake/صف/خاموشی خودکار)
scripts/           wake.sh، scripts/wf/ (منطق workflow + تست واحد)
tests/             ۳۳+ تست + سناریوهای واقعی
docs/              GATEWAY، قرارداد API، ممیزی، محدودیت‌ها، چک‌لیست E2E
```

## امنیت

- **کلید API هرگز به اولین درخواست‌کننده داده نمی‌شود**؛ بازیابی فقط با OAuth گیت‌هاب و فقط برای حساب مالک (`mahdi23179`). Client Secret فقط در Codespace Secrets است و توکن OAuth هرگز ذخیره نمی‌شود (یک‌بار مصرف).
- نمایش/چرخش/ابطال کلید با نشست کوتاه امضاشده (HttpOnly cookie) + هدر CSRF؛ در لاگ‌ها فقط اثر انگشت sha256 ثبت می‌شود.
- ابزارهای عامل: env پاک‌سازی‌شده (توکن‌ها به فرایند فرزند نمی‌رسند)، محدودیت CPU/RAM/زمان با کشتن کل گروه فرایند، الگوهای حساس نیازمند تأیید، لاگ audit در `.state/audit.jsonl`.
- مسیرها با بررسی symlink و commonpath محبوس می‌شوند (باگ prefix traversal قدیمی رفع شد).
- `.gitignore` مسیرهای `.state/` و `*.gguf` را می‌بندد؛ توکن/کلید در هیچ Commit یا لاگی نیست.

</div>

---

<div dir="ltr" align="left">

## English (summary)

**Deldelak** is a fully local AI agent (Persian/English) that runs inside a GitHub Codespace: a Qwen2.5-3B-Instruct GGUF model (~2 GB, Q4_K_M) served by llama.cpp on CPU, an OpenAI-compatible FastAPI, a Planner→Executor→Verifier agent engine with 15 real tools, a Persian RTL chat UI, and SQLite persistence.

**Quick start:** Code → Codespaces → Create → wait ~10-20 min for automatic setup (llama.cpp build + resumable, sha256-verified model download) → the chat UI opens. **Auto-Wake:** anyone can open an Issue containing `!wake` (or the owner runs the *Wake Deldelak Codespace* workflow) — the bot starts, its port is made public, and the link is posted back. See `docs/AUTO_WAKE.md`.

**Limits (honest):** GitHub's free tier gives 120 core-hours/month (≈60 h on 2 cores) and auto-stops idle Codespaces after 30 min (max 240). Expect roughly 5-10 tokens/s on a 2-core machine. Measured numbers: `docs/TEST_REPORT.md`.

**Local dev:** `pip install -r requirements.txt -r requirements-dev.txt && pytest` (22 tests, mocked LLM, no download needed).

</div>

## License

MIT — see [LICENSE](LICENSE).
