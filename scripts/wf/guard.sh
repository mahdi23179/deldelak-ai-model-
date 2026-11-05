#!/usr/bin/env bash
# Deldelak - workflow guard logic, extracted for unit testing (mission §4.3).
# Usage: guard.sh <event_name> [issue_text]
# Prints "RUN=true" or "RUN=false" to stdout. Issue text is NEVER interpolated
# into shell code by callers - it is passed as a quoted argument (CWE-78 fix).
set -u

EVENT="${1:-}"
ISSUE_TEXT="${2:-}"

case "$EVENT" in
  schedule)
    # keep-alive is intentionally removed (mission §3): scheduled runs do nothing.
    echo "RUN=false"
    ;;
  issues)
    # legacy issue-wake is disabled; wake happens only via the Gateway now.
    echo "RUN=false"
    ;;
  workflow_dispatch|repository_dispatch)
    echo "RUN=true"
    ;;
  *)
    echo "RUN=false"
    ;;
esac
