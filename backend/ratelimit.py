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

WINDOW_SECONDS = 60


class RateLimiter:
    def __init__(self, limit_per_window: int):
        self.limit = limit_per_window
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))  # ip -> (window_start, count)

    def check(self, ip: str) -> int:
        """Returns 0 if the request is allowed, else the seconds to wait before retrying."""
        now = int(time.time())
        window = now - (now % WINDOW_SECONDS)
        with self._lock:
            start, count = self._windows[ip]
            if start != window:
                start, count = window, 0
            count += 1
            self._windows[ip] = (start, count)
            if count > self.limit:
                return WINDOW_SECONDS - (now - start)
        return 0

    def prune(self, older_than_seconds: int = 600) -> None:
        """Drop windows old enough that they'll never be read again — call
        periodically so a long-running process doesn't accumulate one entry
        per distinct IP forever."""
        cutoff = int(time.time()) - older_than_seconds
        with self._lock:
            stale = [ip for ip, (start, _) in self._windows.items() if start < cutoff]
            for ip in stale:
                del self._windows[ip]
