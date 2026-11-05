"""Deldelak AI Model - in-process rate limiting (no external deps).

Thread-safe sliding-window counters keyed by (bucket, client_ip).
Used for key-recovery endpoints, the claim oracle and auth-sensitive routes.
Designed for the single-process uvicorn deployment of this project.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

_lock = threading.Lock()
_hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)

# bucket name -> (max_requests, window_seconds)
POLICIES: Dict[str, Tuple[int, float]] = {
    "key_reveal": (20, 60.0),
    "key_rotate": (6, 300.0),
    "oauth_start": (10, 60.0),
    "oauth_callback": (20, 60.0),
    "key_claim": (10, 60.0),      # legacy oracle: keep it tight
    "chat": (60, 60.0),
    "default": (240, 60.0),
}


def _policy(bucket: str) -> Tuple[int, float]:
    return POLICIES.get(bucket, POLICIES["default"])


def check(bucket: str, client_ip: str) -> Tuple[bool, int]:
    """Return (allowed, retry_after_seconds). Sliding window, O(per-window hits)."""
    limit, window = _policy(bucket)
    now = time.time()
    key = (bucket, client_ip or "unknown")
    with _lock:
        q = _hits[key]
        cutoff = now - window
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            retry = max(1, int(window - (now - q[0])) + 1)
            return False, retry
        q.append(now)
        return True, 0


def reset_for_tests() -> None:
    with _lock:
        _hits.clear()
