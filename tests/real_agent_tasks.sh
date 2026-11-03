#!/usr/bin/env bash
# اجرای پنج تسک واقعی عامل دلدلک روی یک سرور زنده (Codespace) + گزارش JSON ماشین‌خوان
#
# Usage:
#   BASE_URL="http://localhost:8000" API_KEY="ddk-xxxx" bash tests/real_agent_tasks.sh
#
# Env vars:
#   BASE_URL      (default: http://localhost:8000)
#   API_KEY       (optional — if empty, tries GET /api/key claim window once)
#   RESULTS_PATH  (default: .state/real_task_results.json)
#   POLL_INTERVAL (default: 5s)   POLL_TIMEOUT (default: 600s)
set -u

BASE_URL="${BASE_URL:-http://localhost:8000}"
RESULTS_PATH="${RESULTS_PATH:-.state/real_task_results.json}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"
POLL_TIMEOUT="${POLL_TIMEOUT:-600}"

if command -v jq >/dev/null 2>&1; then
    HAVE_JQ=1
else
    HAVE_JQ=0
fi

# ---------- tiny JSON helpers (jq if available, else python3) ----------
json_get() { # $1=json text  $2=dotted path e.g. ".status"
    if [ "$HAVE_JQ" -eq 1 ]; then
        printf '%s' "$1" | jq -r "$2 // empty" 2>/dev/null
    else
        printf '%s' "$1" | python3 -c '
import sys, json
path = sys.argv[1].strip(".")
cur = json.load(sys.stdin)
for part in path.split("."):
    if isinstance(cur, dict) and part in cur:
        cur = cur[part]
    else:
        print("")
        raise SystemExit(0)
print(cur if not isinstance(cur, (dict, list)) else json.dumps(cur, ensure_ascii=False))
' "$2"
    fi
}

steps_summary() { # $1=json text → "tool:status;tool:status"
    printf '%s' "$1" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    steps = d.get("steps") or []
    print(";".join(str(s.get("tool")) + ":" + str(s.get("status")) for s in steps))
except Exception:
    print("parse-error")
'
}

mk_payload() { # $1=instruction → compact JSON body
    python3 -c '
import sys, json
print(json.dumps({"instruction": sys.argv[1], "mode": "auto"}, ensure_ascii=False))
' "$1"
}

append_record() { # $1=idx $2=title $3=tid $4=status $5=duration $6=steps $7=error
    python3 -c '
import sys, json
idx, title, tid, status, dur, steps, err = sys.argv[1:8]
rec = {"index": int(idx), "title": title, "task_id": tid, "status": status,
       "duration_s": int(dur), "steps_summary": steps, "error": err,
       "terminal": status in ("completed", "completed_with_issues", "failed"),
       "ok": status == "completed"}
print(json.dumps(rec, ensure_ascii=False))
' "$1" "$2" "$3" "$4" "$5" "$6" "$7" >> "$RECORDS"
}

# ---------- auth ----------
if [ -z "${API_KEY:-}" ]; then
    echo "API_KEY خالی است؛ تلاش برای دریافت از پنجره کلید ($BASE_URL/api/key) ..."
    KEY_RESP="$(curl -sS --max-time 10 "$BASE_URL/api/key" || true)"
    API_KEY="$(json_get "$KEY_RESP" ".key")"
    if [ -z "$API_KEY" ]; then
        echo "خطا: کلید API دریافت نشد. متغیر API_KEY را تنظیم کنید (پنجره ادعا ممکن است بسته باشد)." >&2
        exit 2
    fi
fi
AUTH="Authorization: Bearer $API_KEY"

RECORDS="$(mktemp)"
trap 'rm -f "$RECORDS"' EXIT
mkdir -p "$(dirname "$RESULTS_PATH")" 2>/dev/null || mkdir -p .state

# ---------- the five real tasks ----------
TASK_1="یک پروژه پایتون به نام calc بساز، اجرا کن و تست‌هایش را بگیر"
TASK_2="فایل notes.txt را با چند خط متن فارسی درباره پروژه دلدلک بنویس و سپس با python.execute تعداد کلمات آن را بشمار و نشان بده"
TASK_3="با ابزار python.execute یک اسکریپت کوتاه بنویس که فاکتوریل ۶ را حساب کند و نتیجه ۷۲۰ را چاپ کند"
TASK_4="از پروژه calc یک خروجی zip با project.export بگیر"
TASK_5="در پروژه calc مدیریت تقسیم بر صفر تابع div را بهتر کن، تست‌ها را دوباره اجرا کن و در پایان با git.commit تغییرات را کامیت کن"

FAILED_COUNT=0
INDEX=0

run_task() { # $1=title  $2=instruction
    local title="$1" instruction="$2"
    INDEX=$((INDEX + 1))
    echo "── تسک $INDEX: $title"
    local t0 body tid status err steps
    t0=$(date +%s)
    err=""
    tid=""

    local payload resp
    payload="$(mk_payload "$instruction")"
    resp="$(curl -sS --max-time 30 -X POST "$BASE_URL/v1/agent/tasks" \
        -H "$AUTH" -H "Content-Type: application/json" -d "$payload" 2>&1)" || err="curl-error"
    tid="$(json_get "$resp" ".id")"
    if [ -z "$tid" ]; then
        echo "   خطا در ساخت تسک: $resp"
        append_record "$INDEX" "$title" "" "submit_failed" 0 "" "$resp"
        FAILED_COUNT=$((FAILED_COUNT + 1))
        return
    fi
    echo "   task_id=$tid"

    local deadline
    deadline=$(( $(date +%s) + POLL_TIMEOUT ))
    status=""
    while :; do
        body="$(curl -sS --max-time 30 "$BASE_URL/v1/agent/tasks/$tid" -H "$AUTH" 2>&1)" || body=""
        status="$(json_get "$body" ".status")"
        case "$status" in
            completed|completed_with_issues|failed) break ;;
        esac
        if [ "$(date +%s)" -ge "$deadline" ]; then
            status="timeout"
            err="پاسخ تسک در ${POLL_TIMEOUT} ثانیه قطعی نشد"
            break
        fi
        sleep "$POLL_INTERVAL"
    done

    local dur
    dur=$(( $(date +%s) - t0 ))
    steps="$(steps_summary "$body")"
    if [ "$status" != "completed" ]; then
        FAILED_COUNT=$((FAILED_COUNT + 1))
    fi
    [ -z "$err" ] || err="$(printf '%s' "$err" | head -c 300)"
    echo "   وضعیت=$status مدت=${dur}s گام‌ها=$steps"
    append_record "$INDEX" "$title" "$tid" "$status" "$dur" "$steps" "$err"
}

run_task "ساخت پروژه پایتون calc با تست" "$TASK_1"
run_task "نوشتن notes.txt با متن فارسی و شمارش کلمات" "$TASK_2"
run_task "محاسبه فاکتوریل ۶ با python.execute" "$TASK_3"
run_task "خروجی ZIP پروژه calc" "$TASK_4"
run_task "اصلاح تقسیم بر صفر در calc + تست + کامیت" "$TASK_5"

# ---------- final machine-readable report ----------
python3 - "$RECORDS" "$RESULTS_PATH" <<'PY'
import json
import os
import sys

rec_path, out_path = sys.argv[1], sys.argv[2]
recs = []
with open(rec_path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            recs.append(json.loads(line))
os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
report = {
    "tasks": recs,
    "total": len(recs),
    "completed": sum(1 for r in recs if r["ok"]),
    "not_completed": sum(1 for r in recs if not r["ok"]),
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("گزارش JSON نوشته شد:", out_path)
PY

echo "خلاصه: $((INDEX - FAILED_COUNT)) از $INDEX تسک کامل شد. نتایج: $RESULTS_PATH"
[ "$FAILED_COUNT" -eq 0 ]
