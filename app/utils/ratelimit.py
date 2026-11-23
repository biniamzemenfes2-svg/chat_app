"""A small in-process sliding-window limiter for Socket.IO events.

Flask-Limiter only guards HTTP routes. Real-time events need their own limit so
one connection cannot flood a conversation. State is per process, like the
presence tracker; running several workers needs a shared store (see README).
"""
import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


socket_limiter = SlidingWindowLimiter()
