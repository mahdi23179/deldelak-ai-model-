#!/usr/bin/env bash
# Deldelak - real state verification for wake operations (mission §4.4/4.5).
# Called by the ops workflow with a PAT in GH_TOKEN. Every step prints the
# ACTUAL codespace state; no phantom success: exits non-zero on failure.
# Usage: verify_state.sh <owner/repo> <port>
set -euo pipefail
REPO="$1"
PORT="${2:-8000}"
: "${GH_TOKEN:?GH_TOKEN env var is required (never hardcode it)}"
API="https://api.github.com"
H=(-H "Authorization: Bearer ${GH_TOKEN}" -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28")

step() { printf '\n[verify] %s\n' "$1"; }

step "1/5 list codespaces for ${REPO}"
NAME=$(curl -fsS "${H[@]}" "${API}/repos/${REPO}/codespaces?per_page=100" | \
  jq -r '.codespaces[0].name // empty')
if [ -z "$NAME" ]; then
  echo "[verify] FAIL: no codespace exists for this repo"; exit 3
fi
echo "[verify] codespace: ${NAME}"

step "2/5 read the real state"
STATE=$(curl -fsS "${H[@]}" "${API}/user/codespaces/${NAME}" | jq -r '.state')
echo "[verify] state=${STATE}"

step "3/5 start if not running"
case "$STATE" in
  Running) echo "[verify] already running" ;;
  Stopped|Shutdown|Archived)
    HTTP=$(curl -s -o /tmp/start.out -w "%{http_code}" -X POST "${H[@]}" "${API}/user/codespaces/${NAME}/start")
    echo "[verify] start -> HTTP ${HTTP}"
    [ "$HTTP" = "202" ] || { echo "[verify] FAIL: start rejected"; cat /tmp/start.out; exit 4; }
    ;;
  *) echo "[verify] state ${STATE} is transitional; waiting below" ;;
esac

step "4/5 wait until the API reports model_ready or backend_ready (max 25 min)"
DEADLINE=$(( $(date +%s) + 1500 ))
LAST=""
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  STATE=$(curl -fsS "${H[@]}" "${API}/user/codespaces/${NAME}" | jq -r '.state' || echo "unknown")
  if [ "$STATE" = "Running" ]; then
    BASE="https://${NAME}-${PORT}.app.github.dev"
    CODE=$(curl -s -o /tmp/h.json -w "%{http_code}" --max-time 10 "${BASE}/health" || echo 000)
    if [ "$CODE" = "200" ]; then
      MODEL=$(jq -r '.model_loaded // false' /tmp/h.json 2>/dev/null || echo false)
      LAST="backend_ready=true model_loaded=${MODEL}"
      echo "[verify] ${LAST}"
      if [ "$MODEL" = "true" ]; then echo "[verify] SUCCESS"; exit 0; fi
    fi
  fi
  sleep 20
done
echo "[verify] FAIL: not model-ready within the deadline (last: ${LAST:-no response})"; exit 5

step "5/5 report"
echo "[verify] codespace=${NAME} state=${STATE}"
