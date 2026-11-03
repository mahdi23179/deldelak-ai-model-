"""Deldelak AI Model - synchronous LLM client for the agent worker thread.

The agent engine runs in a plain background thread, so it must NOT share the
async httpx client used by the FastAPI event loop (asyncio primitives are
loop-bound). This client uses blocking httpx - correct and safe in a thread.
"""
from __future__ import annotations

import threading
from typing import Any

import httpx

from . import config
from .llm_client import parse_json_object


class SyncLLM:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0))
        self.last_tok_s: float | None = None

    def _base(self) -> str:
        return f"http://{config.LLM_HOST}:{config.LLM_PORT}"

    def health(self) -> bool:
        try:
            with self._lock:
                r = self._client.get(f"{self._base()}/health", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def chat(self, messages: list[dict], *, max_tokens: int = 900,
             temperature: float = 0.2, **_: Any) -> dict:
        payload = {
            "model": config.MODEL_ID,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        with self._lock:
            r = self._client.post(f"{self._base()}/v1/chat/completions", json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"llama-server HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            tps = data.get("timings", {}).get("predicted_per_second")
            if tps:
                self.last_tok_s = round(float(tps), 2)
        except Exception:
            pass
        return data

    def chat_json(self, messages: list[dict], *, max_tokens: int = 900,
                  temperature: float = 0.2, retries: int = 2) -> dict:
        last_err: Exception | None = None
        for _ in range(retries + 1):
            data = self.chat(messages, max_tokens=max_tokens, temperature=temperature)
            text = _extract(data)
            try:
                return parse_json_object(text)
            except Exception as e:
                last_err = e
                messages = messages + [
                    {"role": "assistant", "content": text[:800]},
                    {"role": "user", "content": "پاسخ قبلی JSON معتبر نبود. فقط و فقط یک شیء JSON معتبر برگردان، بدون هیچ متن اضافه."},
                ]
        raise RuntimeError(f"JSON parsing failed: {last_err}")


def _extract(completion: dict) -> str:
    try:
        return completion["choices"][0]["message"]["content"] or ""
    except Exception:
        return ""
