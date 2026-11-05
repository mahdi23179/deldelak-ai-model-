<div dir="rtl">

# Gateway همیشه‌در‌دسترس (Cloudflare Worker + Durable Object)

این سند معماری نهایی فاز ۲ مأموریت است: **کاربر فقط یک API URL و یک API Key دارد.**

```
کلاینت OpenAI / مرورگر
        │  POST https://<gateway>.workers.dev/v1/chat/completions
        ▼
Cloudflare Worker (همیشه روشن، مستقل از Codespace)
  ├─ GET /healthz  → زنده‌بودن گیت‌وی (بدون تماس خارجی)
  ├─ GET /health   → وضعیت واقعی: codespace_state + backend + model
  ├─ GET /v1/requests/{id} → وضعیت درخواست‌های صف‌شده
  ├─ POST /internal/{register,key-fingerprint,notify-activity,stop} → فقط با shared secret
  └─ بقیه مسیرها → احراز کلید → (در صورت نیاز Wake) → پروکسی به Backend
        │  Durable Object «InstanceDO»: وضعیت، صف، تایمر بی‌کاری، نمونه‌های Cold Start
        ▼
GitHub Codespaces REST API (رسمی):
  POST /user/codespaces/{name}/start   POST /user/codespaces/{name}/stop
  GET  /repos/{owner}/{repo}/codespaces
        ▼
FastAPI :8000 (پورت عمومی کداسپیس) → llama-server :8081 (فقط 127.0.0.1)
```

## جریان درخواست وقتی Codespace خاموش است

1. درخواست کاربر به گیت‌وی می‌رسد (گیت‌وی همیشه بالا است).
2. کلید در لبه بررسی می‌شود (hash؛ در صورت عدم تطابق 401 بدون هیچ هزینه‌ای).
3. درخواست با شناسهٔ یکتا (`X-Request-Id`) در صف Durable Object بافر می‌شود — **گم نمی‌شود**.
4. یک عملیات Wake از طریق API رسمی GitHub آغاز می‌شود؛ درخواست‌های هم‌زمان فقط یکی می‌سازند (single-flight).
5. وضعیت هر ۵ ثانیه از واقعیت خوانده می‌شود: `Running` → سلامت Backend → `model_loaded`.
6. تا `HOLD_SECONDS` (پیش‌فرض ۷۵) پاسخِ واقعیِ مدل به همان اتصال برگردانده می‌شود (عادی و Stream هر دو).
7. اگر Cold Start طولانی‌تر شد: پاسخ **صادقانهٔ** `503` با `Retry-After` و `request_id` برمی‌گردد؛ درخواست در صف می‌ماند و پس از آماده‌شدن اجرا می‌شود. کلاینت می‌تواند بعد از Retry همان درخواست را با همان `X-Request-Id` بفرستد (بدون اجرای تکراری) یا `GET /v1/requests/{id}` را بگیرد.
8. هیچ‌وقت پاسخ ساختگی یا موفقیت جعلی تولید نمی‌شود.

## خاموشی خودکار (Idle Shutdown)

- `IDLE_SHUTDOWN_MINUTES` (پیش‌فرض ۱۰) پس از آخرین فعالیت واقعی.
- شرط توقف: صفر درخواست در حال اجرا + صف خالی + تأیید `/internal/can-stop` از Backend (مدل در حال بارگیری یا تسک عامل = ممنوع).
- عملیات Stop **idempotent** است و خطای GitHub در `last_stop_error` قابل مشاهده است.
- Cron هر ۵ دقیقه فقط alarm گم‌شده پس از deploy را دوباره می‌چیند؛ **هیچ‌گاه Codespace را بیدار نگه نمی‌دارد** (ضد Keep-Alive).

## استقرار یک‌باره (۵ دقیقه)

```bash
cd gateway
npm install
npx wrangler login

# Secret 1: PAT با مجوز Codespaces
#   GitHub → Settings → Developer settings → Fine-grained tokens:
#   Repository access: فقط mahdi23179/deldelak-ai-model-
#   Permissions: Codespaces = Read and write ، Metadata = Read-only
npx wrangler secret put GH_TOKEN

# Secret 2: راز مشترک بین گیت‌وی و Backend (یک رشتهٔ تصادفی)
openssl rand -hex 32   # خروجی را جایی ذخیره کنید؛ مرحلهٔ بعد هم لازم است
npx wrangler secret put GATEWAY_SHARED_SECRET

npx wrangler deploy
# آدرس گیت‌وی: https://deldelak-gateway.<subdomain>.workers.dev
```

سپس در GitHub → مخزن → **Settings → Secrets and variables → Codespaces** این Repository/Codespace secretها را بسازید:

| Secret (Codespace) | مقدار | توضیح |
|---|---|---|
| `DELDELAK_GATEWAY_URL` | `https://deldelak-gateway.<subdomain>.workers.dev` | برای ثبت خودکار Backend |
| `DELDELAK_GATEWAY_SECRET` | همان مقدار `GATEWAY_SHARED_SECRET` | احراز `/internal/*` |
| `DELDELAK_OAUTH_CLIENT_ID` | Client ID یک OAuth App | بازیابی کلید مالک |
| `DELDELAK_OAUTH_CLIENT_SECRET` | Client Secret همان OAuth App | **فقط سمت سرور** |

ساخت OAuth App: GitHub → Settings → Developer settings → OAuth Apps → New:
- Homepage: آدرس گیت‌وی
- Callback: `https://<gateway>.workers.dev/api/key/oauth/callback`
- Scope لازم: فقط `read:user` (هیچ مجوز مخزنی نمی‌خواهد)

### پورت عمومی (یک‌بار برای همیشه)

تعیین visibility پورت در REST API رسمی وجود ندارد (مستند شده در AUDIT A17)؛ یک‌بار از درون Codespace:

```bash
gh codespace ports visibility 8000:public -c "$CODESPACE_NAME"
```

این تنظیم ماندگار است و پس از stop/start حفظ می‌شود.

## متغیرهای قابل تنظیم (wrangler.toml → [vars])

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `IDLE_SHUTDOWN_MINUTES` | `10` | خاموشی پس از این مقدار بی‌کاری |
| `HOLD_SECONDS` | `75` | نگه‌داشتن اتصال کلاینت هنگام Cold Start |
| `START_TIMEOUT_MIN` | `12` | سقف زمان Wake تا خطای صادقانه |
| `CODESPACE_NAME` | خالی | سنجاق کردن کداسپیس خاص (در غیر این صورت اولین کداسپیس مخزن) |
| `CODESPACE_REF` | `main` | برای ساخت کداسپیس جدید |

## سهمیه‌ها و هزینه (بخش ۹ مأموریت — اعداد رسمی)

| مورد | مقدار رسمی | اثر روی این طراحی |
|---|---|---|
| Codespaces Free | ۱۲۰ ساعت-هسته/ماه (۶۰ ساعت با ۲ هسته) + ۱۵GB-ماه ذخیره‌سازی | روشن‌شدن-به-درخواست + خاموشی ۱۰ دقیقه‌ای ≈ فقط زمان استفاده واقعی |
| هزینه Cloudflare Worker Free | ۱۰۰٬۰۰۰ درخواست/روز | Gateway بدون Codespace هم پاسخ می‌دهد؛ عملاً رایگان |
| هزینه Durable Object | در پلن Free موجود (SQLite-backed) | یک DO = یک کداسپیس؛ هزینه‌ای اضافه نمی‌کند |
| Rate limit GitHub API | ۵٬۰۰۰ درخواست/ساعت با PAT | هر Wake چند فراخوانی API مصرف می‌کند؛ ناچیز است |
| Cold Start واقعی | start کداسپیس ~۳۰-۶۰s + بارگیری مدل ۲GB ~۳۰-۹۰s | به همین دلیل مسیر صادقانهٔ 503/Retry-After پیاده شد |
| عمر نشست / ابطال | Codespace متوقف مدل را در دیسک نگه می‌دارد؛ مدل دوباره دانلود نمی‌شود | بارگیری فقط پس از ساخت مجدد کداسپیس |

**هیچ Keep-Alive دائمی در طراحی نیست** — این صریحاً ممنوع بود و حذف شد.

## تست واقعی E2E (پس از استقرار)

`docs/e2e_checklist.md` ۱۶ سناریوی بخش ۸ مأموریت را قدم‌به‌قدم با دستور دقیق پوشش می‌دهد. تست‌های منطقی Gateway (۱۷ سناریو) در `gateway/test/` و بدون استقرار اجرا می‌شوند: `cd gateway && node --test test/controller.test.mjs`

</div>
