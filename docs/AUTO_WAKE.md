<div dir="rtl">

# روشن‌شدن خودکار (Auto-Wake) — نسخهٔ ۲

> این سند جایگزین شده است: مسیر قدیمی (Issue با `!wake` + Keep-Alive زمان‌بندی‌شده) به دلیل
> تزریق اسکریپت، موفقیت صوری و مصرف سهمیه **حذف شد** (docs/AUDIT.md: A8, A9, A10).
> معماری جدید: **[Gateway همیشه‌در‌دسترس](GATEWAY.md)**.

## رفتار جدید (بدون هیچ اقدام دستی)

کاربر فقط به یک آدرس و یک کلید نیاز دارد:

```bash
curl https://<gateway>.workers.dev/v1/chat/completions \
  -H "Authorization: Bearer ddk-..." \
  -d '{"messages":[{"role":"user","content":"سلام"}]}'
```

- Codespace خاموش باشد → Gateway با **Codespaces REST API رسمی** آن را روشن می‌کند.
- درخواست در صف Durable Object حفظ می‌شود و پس از آماده‌شدن اجرا می‌گردد.
- پس از ۱۰ دقیقه بی‌کاری واقعی، Gateway آن را متوقف می‌کند (خاموشی idempotent).
- Issue، `!wake`، Keep-Alive و ping دائمی دیگر وجود ندارند.

## عملیات دستی (فقط مالک، برای موارد استثنا)

- تب Actions → «Codespace ops (start/stop with real state verification)» → Run workflow.
- هر مرحله وضعیت واقعی کداسپیس را گزارش می‌کند و شکست، fail صریح است.
- Secret موردنیاز: `WAKE_TOKEN` = fine-grained PAT با مجوز Codespaces: write روی همین مخزن.

جزئیات کامل: [GATEWAY.md](GATEWAY.md) • محدودیت‌ها: [LIMITS.md](LIMITS.md)

</div>
