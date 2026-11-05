# E2E Checklist - 16 real scenarios (mission §8)

Run AFTER deploying the Gateway (docs/GATEWAY.md). Replace `$GW` with the
workers.dev URL and use the real owner key. Each item records evidence
(command + output) in `docs/evidence/e2e-results.md`.

| # | Scenario | Command / Method | Expected honest result |
|---|----------|------------------|------------------------|
| 1 | Codespace stopped, request sent | `curl -i $GW/v1/models -H "Authorization: Bearer $KEY"` with codespace stopped | 200 (held until ready) OR 503 + Retry-After + request_id if cold start > HOLD_SECONDS |
| 2 | Gateway wakes it | watch `gh api repos/mahdi23179/deldelak-ai-model-/codespaces --jq '.codespaces[0].state'` | Shutdown → Starting → Running without any manual action |
| 3 | Backend becomes ready | `curl $GW/health` | `backend_ready:true` appears while codespace Running |
| 4 | Model actually loads | `curl $GW/health \| jq '.backend.model_loaded, .backend.load_started_at'` | model_loaded:true; last_load_duration_s measured once |
| 5 | Real chat completion | `curl $GW/v1/chat/completions -H "Authorization: Bearer $KEY" -d '{"messages":[{"role":"user","content":"سلام"}]}'` | real model answer (Persian) |
| 6 | Response reaches client | same as 5 | HTTP 200 with choices[0].message.content |
| 7 | Second request without re-wake | repeat 5 immediately | no new start API call (gateway state READY/BUSY); instant response |
| 8 | Concurrent requests | 5 parallel curls of 5 | single wake; all answered; active_requests visible in /health |
| 9 | Stream request | 5 with `"stream":true` | SSE chunks arrive progressively, `data: [DONE]` terminator |
| 10 | Auto-stop after idle | set IDLE_SHUTDOWN_MINUTES=1, wait, check codespace state | state Stopped/Shutdown after idle window; nothing running |
| 11 | New request after stop wakes again | repeat 1 | wake cycle repeats; request preserved |
| 12 | GitHub API error handling | temporarily set GH_TOKEN to an invalid value | gateway returns 502 wake_failed with last_error in /health; recovers after fixing |
| 13 | Cold start timeout | set HOLD_SECONDS=5, stop codespace, request | 503 cold_start_in_progress + Retry-After + estimate; buffered request still executes |
| 14 | Owner key recovery | browser: open UI → settings → ورود با گیت‌هاب | owner session → reveal works |
| 15 | Non-owner rejected | recovery flow from another GitHub account | 403 forbidden, no key material in any response |
| 16 | Key never leaks | `curl -s $GW/v1/models -H "Authorization: Bearer wrong"`; check server logs + audit trail | 401 invalid_api_key; key/fingerprint appears only as sha256 prefix in logs |

## What was verified WITHOUT deployment (this repo, real runs)

- `pytest -q` — full backend suite incl. owner-only key, rotate-revokes-old,
  reveal auth, export tokens, path jail, health fields (see docs/evidence/).
- `cd gateway && node --test test/controller.test.mjs` — 17 scenarios covering
  wake single-flight, queue preservation, hold timeout honesty, idle stop and
  its guards, GH failure handling, stream passthrough, key hash fast-fail.
- `pytest tests/test_06_workflow_logic.py` — workflow guards + injection safety.

The 16 live scenarios above REQUIRE the Cloudflare deploy + real Codespace and
cannot be executed from this offline engineering environment; they are fully
scripted here so the owner can run them in minutes.
