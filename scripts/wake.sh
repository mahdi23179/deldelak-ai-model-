#!/usr/bin/env bash
# Deldelak AI Model - wake the Codespace + activate the chatbot from your own machine.
# Needs: GH_TOKEN env var (fine-grained PAT with Codespaces: write) and curl+jq.
# Usage: GH_TOKEN=ghp_xxx bash scripts/wake.sh [repo] [port]
set -euo pipefail
REPO="${1:-mahdi23179/deldelak-ai-model-}"
PORT="${2:-8000}"
: "${GH_TOKEN:?GH_TOKEN env var is required (never hardcode it)}"
API="https://api.github.com"
H=(-H "Authorization: Bearer ${GH_TOKEN}" -H "Accept: application/vnd.github+json")

echo "[wake] searching for an existing Codespace on ${REPO} ..."
NAME=$(curl -fsS "${H[@]}" "${API}/user/codespaces?per_page=100" | \
  jq -r --arg r "$REPO" '.codespaces[] | select(.repository.full_name==$r) | .name' | head -1 || true)

if [ -z "$NAME" ]; then
  echo "[wake] none found; creating (basicLinux32gb, main) ..."
  NAME=$(curl -fsS "${H[@]}" -X POST "${API}/repos/${REPO}/codespaces" \
    -H "Content-Type: application/json" -d '{"ref":"main","machine_type":"basicLinux32gb"}' | jq -r '.name')
fi
echo "[wake] codespace: ${NAME}"

STATE=$(curl -fsS "${H[@]}" "${API}/user/codespaces/${NAME}" | jq -r '.state')
echo "[wake] state=${STATE}"
case "$STATE" in
  Shutdown|Stopped|Archived)
    curl -fsS "${H[@]}" -X POST "${API}/user/codespaces/${NAME}/start" >/dev/null
    echo "[wake] start requested" ;;
esac

curl -fsS "${H[@]}" -X PUT "${API}/user/codespaces/${NAME}/ports/${PORT}" \
  -H "Content-Type: application/json" -d '{"visibility":"open"}' >/dev/null 2>&1 \
  && echo "[wake] port ${PORT} is public" || echo "[wake] could not set port public (may already be)"

BASE="https://${NAME}-${PORT}.app.github.dev"
echo "[wake] waiting for the chatbot at ${BASE}/health ..."
for i in $(seq 1 75); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${BASE}/health" || echo 000)
  [ "$CODE" = "200" ] && { echo "[wake] ALIVE: ${BASE}"; exit 0; }
  sleep 20
done
echo "[wake] not alive within 25 min (first-time setup may still be running); retry later."
exit 1
