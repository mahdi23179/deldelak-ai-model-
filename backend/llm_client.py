"""Deldelak AI Model - LLM client for the local llama.cpp server (llama-server).

The model server binds to 127.0.0.1 ONLY. This client never leaves the machine.
Supports plain JSON chat and SSE streaming passthrough.
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator, Optional

import httpx

from . import config


class LLMNotReady(RuntimeError):
    pass


def _base() -> str:
    return f"http://{config.LLM_HOST}:{config.LLM_PORT}"


class LLMClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
        self.last_tok_s: Optional[float] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            r = await self._client.get(f"{_base()}/health", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    async def chat(self, messages: list[dict], *, max_tokens: int = 1024,
                   temperature: float = 0.7, stream: bool = False,
                   extra: dict | None = None) -> dict:
        payload: dict[str, Any] = {
            "model": config.MODEL_ID,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if extra:
            payload.update(extra)
        r = await self._client.post(f"{_base()}/v1/chat/completions", json=payload)
        if r.status_code != 200:
            raise LLMNotReady(f"llama-server HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            tps = data.get("timings", {}).get("predicted_per_second")
            if tps:
                self.last_tok_s = round(float(tps), 2)
        except Exception:
            pass
        return data

    async def chat_json(self, messages: list[dict], *, max_tokens: int = 900,
                        temperature: float = 0.2, retries: int = 2) -> dict:
        """Chat expecting a JSON object; robustly extracts the first {...} block."""
        last_err: Exception | None = None
        for _ in range(retries + 1):
            data = await self.chat(messages, max_tokens=max_tokens, temperature=temperature)
            text = extract_text(data)
            try:
                return parse_json_object(text)
            except Exception as e:  # retry once with a strict reminder
                last_err = e
                messages = messages + [
                    {"role": "assistant", "content": text[:800]},
                    {"role": "user", "content": "پاسخ قبلی JSON معتبر نبود. فقط و فقط یک شیء JSON معتبر برگردان، بدون هیچ متن اضافه."},
                ]
        raise LLMNotReady(f"JSON parsing failed: {last_err}")

    async def stream_chat(self, messages: list[dict], *, max_tokens: int = 1024,
                          temperature: float = 0.7) -> AsyncGenerator[bytes, None]:
        payload = {
            "model": config.MODEL_ID,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        async with self._client.stream("POST", f"{_base()}/v1/chat/completions", json=payload) as r:
            if r.status_code != 200:
                body = (await r.aread()).decode("utf-8", "replace")[:300]
                raise LLMNotReady(f"llama-server HTTP {r.status_code}: {body}")
            async for line in r.aiter_lines():
                if not line:
                    continue
                yield (line + "\n").encode("utf-8")


def extract_text(completion: dict) -> str:
    try:
        return completion["choices"][0]["message"]["content"] or ""
    except Exception:
        return ""


def parse_json_object(text: str) -> dict:
    """Extract first balanced JSON object from a text response."""
    text = text.strip()
    # strip markdown fences
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON")


def wait_ready(timeout: int | None = None) -> bool:
    """Synchronous readiness wait used at startup by the server manager."""
    timeout = timeout or config.LLM_START_TIMEOUT
    deadline = time.time() + timeout
    import urllib.request
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{_base()}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1.0)
    return False
