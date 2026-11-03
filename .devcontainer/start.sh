#!/usr/bin/env bash
# Deldelak AI Model - fast start (runs on EVERY Codespace start/restart).
# Idempotent: if the API is already healthy, does nothing.
set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
BLUE='\033[1;36m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${BLUE}[deldelak]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }

healthy() { curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; }

# Self-healing: if the model or llama-server binary is missing (e.g. the first
# postCreate run was interrupted), rerun setup in the BACKGROUND (idempotent -
# it skips finished stages and resumes the model download) while the API boots.
MODEL_OK=false
[ -n "${MODEL_PATH:-}" ] && [ -f "$MODEL_PATH" ] && MODEL_OK=true
[ -x /opt/llama.cpp/build/bin/llama-server ] && [ -f /opt/models/qwen2.5-3b-instruct-q4-k-m.gguf ] && MODEL_OK=true
if [ "$MODEL_OK" != "true" ] && ! pgrep -f ".devcontainer/setup.sh" >/dev/null 2>&1; then
  warn "مدل/llama-server کامل نیست - setup در پس‌زمینه ادامه می‌یابد (لاگ: .state/setup-rerun.log)"
  mkdir -p "$REPO_ROOT/.state"
  nohup bash "$REPO_ROOT/.devcontainer/setup.sh" >> "$REPO_ROOT/.state/setup-rerun.log" 2>&1 &
fi

if healthy; then
  ok "سرویس از قبل فعال است"
else
  log "راه‌اندازی سرور API (پورت ${PORT}) ..."
  mkdir -p "$REPO_ROOT/.state"
  nohup python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT" \
    --app-dir "$REPO_ROOT" >> "$REPO_ROOT/.state/server.log" 2>&1 &
  for i in $(seq 1 30); do
    healthy && break
    sleep 1
  done
  healthy || { warn "سرور بالا نیامد - لاگ:"; tail -20 "$REPO_ROOT/.state/server.log"; exit 1; }
  ok "سرور API فعال شد"
fi

MODEL_HINT="در حال بارگیری"
if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q '"model_loaded":true'; then
  MODEL_HINT="آماده"
else
  warn "مدل هنوز در حال آماده‌سازی است (اولین بار ~۵ تا ۱۵ دقیقه). لاگ: .state/server.log"
fi

LOCAL_URL="http://localhost:${PORT}"
CS_URL=""
if [ -n "${CODESPACE_NAME:-}" ]; then
  CS_URL="https://${CODESPACE_NAME}-${PORT}.app.github.dev"
fi

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  دلدلک | Deldelak AI Model v1.0.0${NC}"
echo -e "${BLUE}============================================================${NC}"
echo "  وب‌چت (UI):      ${CS_URL:-$LOCAL_URL}"
echo "  API محلی:        $LOCAL_URL/v1/chat/completions"
echo "  سلامت سرویس:     $LOCAL_URL/health"
echo "  وضعیت مدل:       $MODEL_HINT"
echo "  کلید API:        فایل .state/api_key  یا  GET ${CS_URL:-$LOCAL_URL}/api/key (فقط بار اول)"
echo -e "${YELLOW}  توجه: کداسپیس‌های شخصی پس از ۳۰ دقیقه بی‌کاری متوقف می‌شوند (حداکثر ۲۴۰ دقیقه).${NC}"
echo -e "${YELLOW}  برای روشن کردن خودکار، بخش Auto-Wake در README را ببینید.${NC}"
echo -e "${BLUE}============================================================${NC}"
