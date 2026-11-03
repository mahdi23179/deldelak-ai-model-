# Deldelak AI Model — API Contract (v1)

Base URL: `http://localhost:8000` (in Codespaces: forwarded port URL).
Auth: all `/v1/*` endpoints require `Authorization: Bearer <key>` **or** `X-API-Key: <key>`.
Public (no auth): `GET /health`, `GET /` (UI), `GET /api/key`, `POST /api/key/claim`,
`GET /v1/projects/{pid}/export?api_key=<key>` (query auth for browser downloads).

## Endpoints

### GET /health → 200
```json
{"status":"ok|degraded","service":"deldelak-ai-model","version":"1.0.0","model_loaded":true,"model":"qwen2.5-3b-instruct-q4-k-m","uptime_s":123.4,"time":"..."}
```

### GET /api/key → 200 once, then 403
```json
{"key":"ddk-xxxx","claimed":true,"note":"..."}
```
First caller claims the key. After claiming: 403 with Persian error.
`POST /api/key/claim {"key":"ddk-xxx"}` → `{"ok":true}` (marks claimed, validates key).
`POST /api/key/rotate` (auth) → `{"key":"...","note":"..."}`.

### GET /v1/models (auth)
```json
{"object":"list","data":[{"id":"qwen2.5-3b-instruct-q4-k-m","object":"model","owned_by":"deldelak-local","ready":true,"label":"Qwen2.5-3B-Instruct (Q4_K_M) - multilingual Persian/English, CPU"}]}
```

### POST /v1/chat/completions (auth) — OpenAI compatible
Request:
```json
{"model":null,"messages":[{"role":"user","content":"سلام"}],"max_tokens":1024,"temperature":0.7,"stream":true,"session_id":"abc123"}
```
- `stream:true` → SSE passthrough (`data: {...}` lines, `data: [DONE]` terminator).
- non-stream → OpenAI response JSON; `model` field is normalized.
- 503 when the local model is not ready yet (Persian message).
- `session_id` (optional): persists user+assistant messages in SQLite.

### Sessions (auth)
- `POST /v1/sessions {"title":"...","mode":"chat"}` → `{"id":"a1b2c3","title":...,"mode":...,"created_at":...}`
- `GET /v1/sessions` → `{"object":"list","data":[{...}]}`
- `GET /v1/sessions/{id}/messages` → `{"object":"list","data":[{"id":1,"session_id":"...","role":"user|assistant","content":"...","created_at":...}]}`
- `DELETE /v1/sessions/{id}` → `{"ok":true}`

### Agent tasks (auth)
- `POST /v1/agent/tasks {"instruction":"یک پروژه پایتون بساز...","mode":"auto"}` → `{"id":"...","instruction":"...","mode":"auto","status":"queued"}`
- `GET /v1/agent/tasks` → `{"object":"list","data":[task,...]}` (each with `steps_count`)
- `GET /v1/agent/tasks/{id}` → full detail:
```json
{"id":"...","instruction":"...","mode":"auto","status":"executing","result":null,"error":null,"cycles":1,"created_at":1730000000.0,"updated_at":1730000100.0,
 "steps":[{"id":1,"task_id":"...","phase":"execute","seq":1,"tool":"project.create","title":"ساخت پروژه","args":{"name":"calc"},"output":"...","status":"ok","started_at":...,"finished_at":...}],
 "checkpoints":3}
```
Task status enum: `queued | planning | executing | verifying | awaiting_approval | completed | completed_with_issues | failed`.
Step status enum: `pending | running | awaiting_approval | ok | error`.
When `status=="awaiting_approval"` the UI must show Approve/Deny buttons →
`POST /v1/agent/tasks/{id}/approve {"approved":true}` → `{"ok":true,"approved":true}` (409 if not waiting).
`result` (when completed) is a JSON **string**: `{"summary":"...","verifier":{...}}`.

### GET /v1/system/status (auth)
```json
{"status":"ok","version":"1.0.0","uptime_s":600.0,
 "platform":{"os":"Linux 6.x","machine":"x86_64","cpus":2,"python":"3.11.x"},
 "ram":{"used_mb":3210.5,"total_mb":7757.8,"percent":41.4},
 "disk":{"used_gb":12.3,"total_gb":31.5,"percent":39.0},
 "cpu_percent":15.2,"load_avg":[0.4,0.5,0.6],
 "model":{"id":"...","loaded":true,"llama_rss_mb":2600.0,"size_mb":1990.0,"ctx":4096,"threads":2},
 "llm":{"host":"127.0.0.1","port":8081,"last_tok_s":7.4,"local_only":true},
 "agent":{"active_tasks":0,"worker_alive":false},
 "codespace":{"name":"...","region":"..."}}
```

### Files (auth) — relative to the workspace dir
- `GET /v1/files?path=.` → `{"path":".","items":[{"name":"calc","path":"calc","type":"dir|file","size":123,"mtime":...}]}`
- `GET /v1/files/content?path=calc/main.py` → `{"path":"...","content":"..."}`
- `PUT /v1/files/content {"path":"calc/main.py","content":"..."}` → `{"ok":true,"path":"...","size":...}`
- `DELETE /v1/files?path=calc/old.py` → `{"ok":true}`

### Projects (auth unless noted)
- `GET /v1/projects` → `{"object":"list","data":[{"id":"...","name":"calc","path":"...","template":"python","status":"created","files_count":5}]}`
- `POST /v1/projects {"name":"calc","template":"python|node"}` → `{"ok":true,"message":"...","project":{...}}`
- `GET /v1/projects/{pid}/export?api_key=<key>` → ZIP file download (FileResponse)
- `POST /v1/projects/{pid}/run` → `{"ok":true,"output":"...","exit_code":0}`
- `POST /v1/projects/{pid}/test` → `{"ok":true,"output":"...","exit_code":0}`

## Frontend design tokens (GitHub dark palette)
```
--bg:#0D1117  --panel:#161B22  --border:#30363D  --text:#E6EDF3  --muted:#9198A1
--accent:#1F6FEB  --green:#238636  --red:#F85149  --yellow:#D29922
```
Font: Vazirmatn (fa) — CDN `https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css` with system fallback.
Icons: inline SVG only. **No emoji anywhere.** RTL by default (`dir="rtl"`), fa/en toggle.
