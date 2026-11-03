"""Deldelak AI Model - llama.cpp server process manager.

Starts `llama-server` bound to 127.0.0.1 (never exposed). If the model or the
binary is missing (e.g. unit-test environment), the API still boots and reports
`model_loaded: false` with a clear Persian message - honest degradation.
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


class LLMServerManager:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.started_at: Optional[float] = None
        self.last_error: Optional[str] = None

    def binary_available(self) -> bool:
        return config.LLAMA_SERVER_BIN.exists() and os.access(config.LLAMA_SERVER_BIN, os.X_OK)

    def model_available(self) -> bool:
        return config.MODEL_PATH.exists() and config.MODEL_PATH.stat().st_size > 50 * 1024 * 1024

    def start(self, wait: bool = True) -> bool:
        """Start llama-server in a background thread. Returns immediately if impossible."""
        if not self.binary_available() or not self.model_available():
            self.last_error = (
                "llama-server یا مدل هنوز آماده نیست "
                f"(binary={config.LLAMA_SERVER_BIN.exists()}, model={config.MODEL_PATH.exists()}). "
                "اسکریپت .devcontainer/setup.sh را اجرا کنید."
            )
            return False
        t = threading.Thread(target=self._spawn, daemon=True)
        t.start()
        if wait:
            return self.wait_until_ready()
        return True

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
                self.proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.started_at = time.time()
            except Exception as e:
                self.last_error = f"failed to spawn llama-server: {e}"

    def wait_until_ready(self) -> bool:
        ok = wait_ready(config.LLM_START_TIMEOUT)
        if not ok:
            self.last_error = self.last_error or "llama-server در مهلت تعیین‌شده آماده نشد"
        return ok

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

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
