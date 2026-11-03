#!/usr/bin/env bash
# اجرای کامل تست‌های «دلدلک AI Model» + نوشتن گزارش فارسی در .state/test_report.txt
# خروجی: کد خروج pytest (۰ = همه سبز)
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
mkdir -p .state

LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

python3 -m pytest tests -q --tb=short "$@" 2>&1 | tee "$LOG"
CODE=${PIPESTATUS[0]}

python3 - "$LOG" "$CODE" .state/test_report.txt <<'PY'
import re
import sys

log_path, code, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(log_path, encoding="utf-8", errors="replace").read()

passed = 0
m = re.findall(r"(\d+) passed", text)
if m:
    passed = int(m[-1])
failed = 0
for key in ("failed", "error"):
    mm = re.findall(r"(\d+) " + key, text)
    if mm:
        failed += int(mm[-1])

fa = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
total = passed + failed
if code == "0" and failed == 0:
    summary = f"{str(total).translate(fa)} تست اجرا شد، همه سبز"
else:
    summary = (f"{str(total).translate(fa)} تست اجرا شد؛ "
               f"{str(failed).translate(fa)} مورد قرمز (کد خروج {str(code).translate(fa)})")

with open(out_path, "w", encoding="utf-8") as f:
    f.write(summary + "\n")
print("گزارش:", summary)
PY

exit "$CODE"
