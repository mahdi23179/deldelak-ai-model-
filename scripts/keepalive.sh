#!/usr/bin/env bash
# Deldelak AI Model - keep the Codespace awake by pinging the public chatbot URL.
# Any request to a forwarded port resets the Codespace idle timer, so a light
# periodic ping keeps the bot alive (uses your free core-hour quota!).
# Usage: bash scripts/keepalive.sh https://<name>-8000.app.github.dev [interval_s]
# Run it from your machine, cron, or a free uptime monitor (cron-job.org / UptimeRobot).
set -u
BASE="${1:?usage: keepalive.sh https://<name>-8000.app.github.dev [interval_s]}"
INTERVAL="${2:-1500}"
while true; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${BASE}/health" || echo 000)
  echo "$(date '+%F %T') ping ${BASE}/health -> ${CODE}"
  sleep "$INTERVAL"
done
