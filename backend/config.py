"""Deldelak AI Model - configuration (env-driven, safe defaults for GitHub Codespaces)."""
from __future__ import annotations

import os
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
KEY_CLAIMED_FILE = STATE_DIR / "key_claimed"

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
)


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


VERSION = "1.0.0"
