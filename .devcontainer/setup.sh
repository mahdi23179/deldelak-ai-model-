#!/usr/bin/env bash
# Deldelak AI Model - one-shot Codespace setup (runs once per Codespace creation).
# Flow: system deps -> python deps -> build llama.cpp -> download model (resumable + sha256)
#       -> generate API key -> start API server -> health check.
# The model is NOT stored in git; it is downloaded here on first boot and cached
# in /opt/models. Re-running this script skips everything already done.
set -euo pipefail

BLUE='\033[1;36m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; NC='\033[0m'
log()  { echo -e "${BLUE}[deldelak]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
fail() { echo -e "${RED}[fail]${NC} $*"; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_REPO="${MODEL_REPO:-Qwen/Qwen2.5-3B-Instruct-GGUF}"
MODEL_PATTERN="${MODEL_PATTERN:-q4-k-m}"
MODELS_DIR="${MODELS_DIR:-/opt/models}"
LLAMA_DIR="${LLAMA_DIR:-/opt/llama.cpp}"
LLAMA_REPO="https://github.com/ggerganov/llama.cpp"

log "مرحله ۱/۶: وابستگی‌های سیستمی"
if command -v sudo >/dev/null 2>&1; then SUDO=sudo; else SUDO=; fi
$SUDO apt-get update -qq >/dev/null 2>&1 || warn "apt-get update failed (continuing)"
$SUDO apt-get install -y -qq build-essential cmake curl git jq >/dev/null 2>&1 || \
  warn "apt-get install failed; existing tools will be used"
ok "ابزارهای سیستمی آماده‌اند"

log "مرحله ۲/۶: وابستگی‌های پایتون"
pip install -q --upgrade pip >/dev/null 2>&1 || true
pip install -q -r "$REPO_ROOT/requirements.txt" || fail "pip install failed"
ok "پایتون آماده است"

log "مرحله ۳/۶: ساخت llama.cpp"
if [ -x "$LLAMA_DIR/build/bin/llama-server" ]; then
  ok "llama-server از قبل ساخته شده است"
else
  # /opt needs root: create the dir and hand it to the container user
  $SUDO mkdir -p "$LLAMA_DIR"
  $SUDO chown -R "$(id -u):$(id -g)" "$LLAMA_DIR" 2>/dev/null || true
  if [ ! -d "$LLAMA_DIR/.git" ]; then
    $SUDO rm -rf "$LLAMA_DIR" 2>/dev/null || true
    $SUDO mkdir -p "$LLAMA_DIR"
    $SUDO chown -R "$(id -u):$(id -g)" "$LLAMA_DIR" 2>/dev/null || true
    git clone --depth 1 "$LLAMA_REPO" "$LLAMA_DIR" >/dev/null 2>&1 || fail "git clone llama.cpp failed"
  fi
  cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build "$LLAMA_DIR/build" --target llama-server -j2 >/dev/null 2>&1 || \
    cmake --build "$LLAMA_DIR/build" --target llama-server -j1 || fail "llama.cpp build failed"
  ok "llama-server ساخته شد: $LLAMA_DIR/build/bin/llama-server"
fi

log "مرحله ۴/۶: بارگیری مدل (قابل ادامه/Resume، بدون دانلود تکراری)"
$SUDO mkdir -p "$MODELS_DIR"
$SUDO chmod 777 "$MODELS_DIR" 2>/dev/null || true
MODEL_PATH="${MODEL_PATH:-$MODELS_DIR/${MODEL_PATTERN}.gguf}"

sha_ok() {  # file expected_sha
  local f="$1" want="$2"
  [ -f "$f" ] || return 1
  echo "$want  $f" | sha256sum -c --quiet >/dev/null 2>&1
}

if [ -f "$MODEL_PATH" ]; then
  ok "فایل مدل موجود است: $MODEL_PATH ($(du -h "$MODEL_PATH" | cut -f1))"
fi

# Resolve model file list + sha256 from HuggingFace metadata (no hardcoded hash)
META=$(curl -fsSL "https://huggingface.co/api/models/${MODEL_REPO}/tree/main" 2>/dev/null || echo "[]")
FILES=$(echo "$META" | jq -r --arg pat "$MODEL_PATTERN" \
  '.[] | select(.path | test($pat)) | select(.path | endswith(".gguf")) | [.path, (.lfs.oid // ""), (.lfs.size // 0)] | @tsv' 2>/dev/null || true)

if [ -z "$FILES" ]; then
  warn "متادیتای HuggingFace خوانده نشد؛ دانلود مستقیم تلاش می‌شود"
  DEST="$MODEL_PATH"
  curl -L -C - --retry 5 --retry-delay 3 -o "$DEST" \
    "https://huggingface.co/${MODEL_REPO}/resolve/main/qwen2.5-3b-instruct-q4-k-m.gguf" \
    || fail "دانلود مدل ناموفق بود؛ این اسکریپت را دوباره اجرا کنید (ادامه از محل قطع)"
else
  n=0
  while IFS=$'\t' read -r fname oid fsize; do
    n=$((n+1))
    part_base="$MODELS_DIR/${fname}"
    url="https://huggingface.co/${MODEL_REPO}/resolve/main/${fname}"
    if sha_ok "$part_base" "$oid"; then
      ok "بخش $n از قبل کامل و سالم است: $fname"
      continue
    fi
    log "بارگیری $fname ($(( fsize / 1048576 )) MB) ..."
    curl -L -C - --retry 8 --retry-delay 3 --retry-all-errors -o "$part_base" "$url" \
      || fail "دانلود $fname ناموفق بود؛ دوباره اجرا کنید - ادامه از محل قطع"
    if [ -n "$oid" ] && ! sha_ok "$part_base" "$oid"; then
      warn "چک‌سام بخش $n درست نیست؛ دوباره بارگیری می‌شود"
      curl -L -C - --retry 8 --retry-delay 3 --retry-all-errors -o "$part_base" "$url" || fail "retry failed"
      sha_ok "$part_base" "$oid" || fail "چک‌سام sha256 برقرار نشد: $fname"
    fi
    ok "بخش $n سالم است (sha256 تأیید شد)"
  done <<< "$FILES"
  # single file vs split parts
  FIRST=$(echo "$FILES" | head -1 | cut -f1)
  if echo "$FILES" | grep -q "of-000"; then
    ok "مدل چندبخشی است؛ llama-server مستقیماً بخش اول را می‌خواند"
    MODEL_PATH="$MODELS_DIR/$FIRST"
  else
    MODEL_PATH="$MODELS_DIR/$FIRST"
  fi
fi
ok "مدل نهایی: $MODEL_PATH ($(du -h "$MODEL_PATH" 2>/dev/null | cut -f1 || echo '?'))"

log "مرحله ۵/۶: کلید API"
mkdir -p "$REPO_ROOT/.state"
if [ ! -f "$REPO_ROOT/.state/api_key" ]; then
  python3 -c "import secrets;print('ddk-'+secrets.token_urlsafe(24))" > "$REPO_ROOT/.state/api_key"
  chmod 600 "$REPO_ROOT/.state/api_key"
fi
ok "کلید API آماده است (فایل .state/api_key + GET /api/key)"

log "مرحله ۶/۶: راه‌اندازی سرویس"
bash "$REPO_ROOT/.devcontainer/start.sh"
