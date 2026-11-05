"""Admin authentication: password login -> short-lived HMAC-signed bearer token, plus a login rate limiter."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from collections import defaultdict, deque


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(secret: str, ttl_minutes: int, subject: str = "admin") -> str:
    payload = _b64(json.dumps({"sub": subject, "exp": int(time.time()) + ttl_minutes * 60}).encode())
    sig = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(secret: str, token: str) -> str | None:
    try:
        payload, sig = token.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_unb64(payload))
        return data["sub"] if data["exp"] > time.time() else None
    except Exception:  # noqa: BLE001
        return None


def check_password(expected: str, given: str) -> bool:
    return bool(expected) and hmac.compare_digest(hashlib.sha256(expected.encode()).digest(), hashlib.sha256((given or "").encode()).digest())


class RateLimiter:
    """Sliding window: at most `limit` attempts per `window` seconds per key."""

    def __init__(self, limit: int = 8, window: int = 300):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True
