"""Per-IP fixed-window rate limiter — a dict and a lock, not a dependency.

ThreadingHTTPServer means concurrent requests from different IPs hit this at
once, so the shared window dict needs a lock. Fixed-window (not sliding/token
bucket) is the deliberate simplification here: it lets a client burst up to
2x the limit right at a window boundary, which is fine for "stop one runaway
client on the LAN" and not fine for billing-grade fairness — this app is the
former.
"""
import threading
import time
from collections import defaultdict

WINDOW_SECONDS = 60  # default window; pass another to the constructor


class RateLimiter:
    def __init__(self, limit_per_window: int, window_seconds: int = WINDOW_SECONDS):
        self.limit = limit_per_window
        self.window = window_seconds
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))  # ip -> (window_start, count)

    def check(self, ip: str) -> int:
        """Returns 0 if the request is allowed, else the seconds to wait before retrying."""
        now = int(time.time())
        window = now - (now % self.window)
        with self._lock:
            start, count = self._windows[ip]
            if start != window:
                start, count = window, 0
            count += 1
            self._windows[ip] = (start, count)
            if count > self.limit:
                return self.window - (now - start)
        return 0

    def prune(self, older_than_seconds: int | None = None) -> None:
        """Drop windows old enough that they'll never be read again — call
        periodically so a long-running process doesn't accumulate one entry
        per distinct IP forever."""
        cutoff = int(time.time()) - (older_than_seconds or self.window * 10)
        with self._lock:
            stale = [ip for ip, (start, _) in self._windows.items() if start < cutoff]
            for ip in stale:
                del self._windows[ip]
