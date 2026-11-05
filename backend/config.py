"""Deldelak AI Model - configuration (env-driven, safe defaults for GitHub Codespaces)."""
from __future__ import annotations

import os
import re
import pathlib

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
STATE_DIR = pathlib.Path(os.getenv("DELDELAK_STATE", str(BASE_DIR / ".state")))
WORKSPACE_DIR = pathlib.Path(os.getenv("DELDELAK_WORKSPACE", str(pathlib.Path.home() / "deldelak-workspace")))

# llama.cpp runtime
LLAMA_DIR = pathlib.Path(os.getenv("LLAMA_DIR", "/opt/llama.cpp"))
LLAMA_SERVER_BIN = pathlib.Path(os.getenv("LLAMA_SERVER_BIN", str(LLAMA_DIR / "build" / "bin" / "llama-server")))
MODEL_PATH = pathlib.Path(os.getenv("MODEL_PATH", "/opt/models/qwen2.5-3b-instruct-q4-k-m.gguf"))
MODEL_ID = os.getenv("MODEL_ID", "qwen2.5-3b-instruct-q4-k-m")
MODEL_LABEL = os.getenv("MODEL_LABEL", "Qwen2.5-3B-Instruct (Q4_K_M) - multilingual Persian/English, CPU")

# Internal model server: localhost ONLY. Never exposed. Only the FastAPI port is forwarded.
LLM_HOST = "127.0.0.1"
LLM_PORT = int(os.getenv("LLM_PORT", "8081"))
LLM_CTX = int(os.getenv("LLM_CTX", "4096"))
LLM_THREADS = int(os.getenv("LLM_THREADS", "2"))
LLM_START_TIMEOUT = int(os.getenv("LLM_START_TIMEOUT", "120"))

API_PORT = int(os.getenv("PORT", "8000"))
API_HOST = os.getenv("API_HOST", "0.0.0.0")

DB_PATH = STATE_DIR / "deldelak.db"
API_KEY_FILE = STATE_DIR / "api_key"
KEY_CLAIMED_FILE = STATE_DIR / "key_claimed"  # legacy, no longer used for access control

# ---- owner-only key recovery (GitHub OAuth) ----
OWNER_LOGIN = os.getenv("DELDELAK_OWNER_LOGIN", "mahdi23179")
OAUTH_CLIENT_ID = os.getenv("DELDELAK_OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("DELDELAK_OAUTH_CLIENT_SECRET", "")  # Codespace secret ONLY
OAUTH_REDIRECT_URI = os.getenv("DELDELAK_OAUTH_REDIRECT_URI", "")    # computed at boot when empty

# ---- gateway integration ----
GATEWAY_URL = os.getenv("DELDELAK_GATEWAY_URL", "").rstrip("/")
GATEWAY_SHARED_SECRET = os.getenv("DELDELAK_GATEWAY_SECRET", "")     # Codespace + Cloudflare secret
PUBLIC_PORT_URL = os.getenv("DELDELAK_PUBLIC_PORT_URL", "")         # auto-computed from CODESPACE_NAME

# ---- request/agent limits ----
MAX_FILE_BODY_BYTES = int(os.getenv("DELDELAK_MAX_FILE_BODY", str(2 * 1024 * 1024)))
MAX_CHAT_BODY_BYTES = int(os.getenv("DELDELAK_MAX_CHAT_BODY", str(512 * 1024)))
DOWNLOAD_TOKEN_TTL_S = int(os.getenv("DELDELAK_DL_TOKEN_TTL", "300"))
DOCS_ENABLED = os.getenv("DELDELAK_DOCS", "0") == "1"

# audit log for agent tool executions
AUDIT_LOG_FILE = STATE_DIR / "audit.jsonl"

EXPORTS_DIR = WORKSPACE_DIR / "exports"

# Agent limits (honest resource budgets for 2-core/8GB Codespaces)
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "12"))
AGENT_MAX_CYCLES = int(os.getenv("AGENT_MAX_CYCLES", "3"))
AGENT_STEP_TIMEOUT = int(os.getenv("AGENT_STEP_TIMEOUT", "120"))
AGENT_TASK_BUDGET = int(os.getenv("AGENT_TASK_BUDGET", "600"))
AGENT_APPROVAL_TIMEOUT = int(os.getenv("AGENT_APPROVAL_TIMEOUT", "1800"))
AGENT_LLM_MAX_TOKENS = int(os.getenv("AGENT_LLM_MAX_TOKENS", "900"))

SENSITIVE_PATTERNS = (
    "rm -rf /", "rm -rf ~", "rm -fr /", "mkfs", "shutdown", "reboot", "halt",
    "dd if=", ":(){", "chmod -R 777 /", "> /dev/sda", "curl | sh", "curl | bash",
    "wget | sh", "wget | bash", "sudo rm", "sudo dd", "passwd", "history -c",
    "sudo", "env |", "printenv", "/proc/", ".ssh", "authorized_keys", "base64 -d",
)

# variables a child process may inherit (allowlist approach - secrets never leak)
CHILD_ENV_ALLOW = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM",
    "TMPDIR", "PWD", "CODESPACE_NAME", "CODESPACE_REGION", "PYTHONUNBUFFERED",
)


def child_env() -> dict:
    """Scratch environment for agent child processes: allowlist + scrub.
    Keys are split into word parts; any part naming a secret category
    (TOKEN/SECRET/KEY/PAT/CRED...) is dropped even if an allowlist entry
    would otherwise leak it (PATH contains 'PAT' and must survive)."""
    _BAD_PARTS = {"TOKEN", "TOKENS", "SECRET", "SECRETS", "KEY", "KEYS",
                  "PAT", "PATS", "CRED", "CREDS", "CREDENTIAL", "CREDENTIALS"}
    out = {}
    for k, v in os.environ.items():
        if k not in CHILD_ENV_ALLOW:
            continue
        parts = {p.upper() for p in re.split(r"[^A-Za-z]+", k) if p}
        if parts & _BAD_PARTS:
            continue
        out[k] = v
    out["DEBIAN_FRONTEND"] = "noninteractive"
    out["PYTHONDONTWRITEBYTECODE"] = "1"
    return out


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def public_base_url() -> str:
    """Best-effort public URL of port 8000 for this Codespace.
    Port visibility persists per codespace; it is set once via gh CLI (see docs)."""
    if PUBLIC_PORT_URL:
        return PUBLIC_PORT_URL.rstrip("/")
    name = os.getenv("CODESPACE_NAME", "")
    port = API_PORT
    return f"https://{name}-{port}.app.github.dev" if name else f"http://127.0.0.1:{port}"


VERSION = "1.0.0"
