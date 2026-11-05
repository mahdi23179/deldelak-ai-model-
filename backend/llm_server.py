"""Deldelak AI Model - llama.cpp server process manager.

Starts `llama-server` bound to 127.0.0.1 (never exposed). If the model or the
binary is missing (e.g. unit-test environment), the API still boots and reports
`model_loaded: false` with a clear Persian message - honest degradation.

v2 additions (mission §5):
- explicit `model_loading` state + load timing (measured, not estimated)
- llama-server stdout/stderr captured to .state/llama-server.log (observable)
- single-load guarantee: concurrent start() calls share one spawn attempt
- corrupt/missing model recovery is delegated to .devcontainer/setup.sh which
  verifies sha256 from HuggingFace metadata and resumes/repairs the download
"""
from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

from . import config
from .llm_client import wait_ready

LLAMA_LOG = config.STATE_DIR / "llama-server.log"
LOG_TAIL_BYTES = 256 * 1024


class LLMServerManager:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.started_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.load_started_at: Optional[float] = None
        self.load_ready_at: Optional[float] = None
        self.last_load_duration_s: Optional[float] = None
        self._spawn_lock = threading.Lock()  # guarantees exactly-one load attempt

    # ---------------- availability ----------------
    def binary_available(self) -> bool:
        return config.LLAMA_SERVER_BIN.exists() and os.access(config.LLAMA_SERVER_BIN, os.X_OK)

    def model_available(self) -> bool:
        return config.MODEL_PATH.exists() and config.MODEL_PATH.stat().st_size > 50 * 1024 * 1024

    # ---------------- lifecycle ----------------
    def start(self, wait: bool = True) -> bool:
        """Start llama-server exactly once (concurrent callers share the attempt)."""
        with self._spawn_lock:
            if self.is_running():
                return True
            if not self.binary_available() or not self.model_available():
                self.last_error = (
                    "llama-server یا مدل هنوز آماده نیست "
                    f"(binary={config.LLAMA_SERVER_BIN.exists()}, model={config.MODEL_PATH.exists()}). "
                    "اسکریپت .devcontainer/setup.sh را اجرا کنید."
                )
                return False
            self.load_started_at = time.time()
            self.load_ready_at = None
            self.last_error = None
            t = threading.Thread(target=self._spawn_and_wait, daemon=True)
            t.start()
        if wait:
            ok = self._wait_ready_blocking()
            if ok:
                self.last_load_duration_s = round(
                    (self.load_ready_at or time.time()) - (self.load_started_at or time.time()), 2)
            return ok
        return True

    def _spawn_and_wait(self) -> None:
        self._spawn()
        self._wait_ready_blocking()

    def _spawn(self) -> None:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            cmd = [
                str(config.LLAMA_SERVER_BIN),
                "-m", str(config.MODEL_PATH),
                "--host", config.LLM_HOST,
                "--port", str(config.LLM_PORT),
                "-c", str(config.LLM_CTX),
                "-t", str(config.LLM_THREADS),
                "--jinja",
                "--mlock" if sys.platform == "linux" and shutil.which("mlock") else "--no-mlock",
                "--metrics",
            ]
            try:
                config.ensure_dirs()
                logf = open(LLAMA_LOG, "ab", buffering=0)
                logf.write(f"\n===== spawn {time.strftime('%Y-%m-%dT%H:%M:%S')} =====\n".encode())
                self.proc = subprocess.Popen(
                    cmd, stdout=logf, stderr=logf,
                    start_new_session=True,
                )
                self.started_at = time.time()
            except Exception as e:
                self.last_error = f"failed to spawn llama-server: {e}"

    def _wait_ready_blocking(self) -> bool:
        ok = wait_ready(config.LLM_START_TIMEOUT)
        if ok:
            self.load_ready_at = time.time()
            self.last_error = None
        else:
            if self.proc and self.proc.poll() is not None:
                self.last_error = f"llama-server exited early (code={self.proc.returncode}); see .state/llama-server.log"
            else:
                self.last_error = self.last_error or "llama-server در مهلت تعیین‌شده آماده نشد"
        return ok

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def is_loading(self) -> bool:
        """Between spawn and /health 200 - the honest 'loading' window."""
        return (self.load_started_at is not None and self.load_ready_at is None
                and not self.is_running_error_exited())

    def is_running_error_exited(self) -> bool:
        return self.proc is not None and self.proc.poll() is not None and self.load_ready_at is None

    def log_tail(self, max_bytes: int = 4000) -> str:
        try:
            with open(LLAMA_LOG, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                return f.read().decode("utf-8", "replace")
        except Exception:
            return ""

    def rss_mb(self) -> float:
        """Resident memory of llama-server in MB (measured, not estimated)."""
        if not self.is_running():
            return 0.0
        try:
            with open(f"/proc/{self.proc.pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return round(int(line.split()[1]) / 1024, 1)
        except Exception:
            pass
        return 0.0

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    pass


manager = LLMServerManager()
atexit.register(manager.stop)
