<div dir="rtl">

# روشن‌شدن خودکار کداسپیس (Auto-Wake)

هدف: **وقتی کاربری درخواست می‌زند، کداسپیس خودبه‌خود روشن شود و چت‌بات فعال شود.**

## سه راه بیدارسازی

### ۱) دکمه Run workflow (صاحب مخزن)

- تب **Actions** → «Wake Deldelak Codespace» → **Run workflow**.
- workflow کداسپیس موجود را روشن می‌کند (یا اگر نبود می‌سازد)، پورت 8000 را عمومی می‌کند، idle timeout را تا ۲۴۰ دقیقه بالا می‌برد و صبر می‌کند تا `/health` پاسخ 200 بدهد.
- لینک عمومی چت‌بات در **خلاصه اجرا (Job Summary)** ظاهر می‌شود.

### ۲) Issue با کلیدواژه `!wake` (هر کسی — بدون توکن)

هر کاربری (حتی بدون لاگین بودن به مخزن؟ خیر — ساخت Issue عمومی نیازی به اجازه ندارد) می‌تواند یک Issue با عنوان یا بدنه حاوی `!wake` بسازد:

1. workflow بلافاصله اجرا می‌شود.
2. کداسپیس روشن و پورت عمومی می‌شود.
3. لینک چت‌بات به‌صورت کامنت روی همان Issue ثبت می‌شود.

این نزدیک‌ترین چیز به «درخواست کاربر → روشن شدن» بدون نیاز به سرور واسط است.

### ۳) repository_dispatch (اتوماسیون)

```bash
curl -X POST \
  -H "Authorization: token $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/mahdi23179/deldelak-ai-model-/dispatches \
  -f event_type=wake-bot
```

یا از کامپیوتر خودتان: `bash scripts/wake.sh` (نیازمند `GH_TOKEN` با مجوز Codespaces).

## راه‌اندازی یک‌باره برای صاحب مخزن

workflow به یک توکن با مجوز **Codespaces: write** نیاز دارد. کافی است یک Actions Secret به نام `WAKE_TOKEN` با مقدار PAT بسازید:

```
Settings → Secrets and variables → Actions → New repository secret
Name:  WAKE_TOKEN
Value: <پات با مجوز Contents:read + Codespaces:write>
```

اگر `WAKE_TOKEN` تعریف نشده باشد، workflow از `GITHUB_TOKEN` داخلی استفاده می‌کند که در بسیاری از موارد برای کداسپیس‌های متعلق به خود مخزن کافی است — ولی اگر خطای مجوز دیدید، secret بالا را بسازید.

## Keep-Alive (نگه‌داشتن روشن)

هر درخواست HTTP به پورت فورواردشده، تایمر خاموشی را صفر می‌کند:

- **خودِ استفاده‌کننده‌ها** زنده نگهش می‌دارند؛
- `scripts/keepalive.sh https://<name>-8000.app.github.dev [interval]` از سیستم خودتان یا سرویس‌های ping رایگان؛
- متغیر مخزن `KEEPALIVE_ENABLED=true` → زمان‌بند داخل workflow هر ۲۵ دقیقه ping می‌کند (فقط وقتی متغیر true باشد اجرا می‌شود).

## هشدار سهمیه

نگه‌داشتن دائمی = مصرف سهمیه. با پلن رایگان (۱۲۰ ساعت-هسته/ماه) ربات ۲ هسته‌ای فقط **۶۰ ساعت در ماه** می‌تواند روشن بماند. پیشنهاد صادقانه: Auto-Wake را فعال نگه دارید (روشن شدن با درخواست) و Keep-Alive دائمی را فقط برای بازه‌های لازم فعال کنید.

</div>
