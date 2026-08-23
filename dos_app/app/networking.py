from __future__ import annotations

import threading
import time


class BandwidthLimiter:
    def __init__(self, bytes_per_second: int | None) -> None:
        self.bytes_per_second = bytes_per_second if bytes_per_second and bytes_per_second > 0 else None
        self._lock = threading.Lock()
        self._tokens = float(self.bytes_per_second or 0)
        self._last_refill = time.monotonic()
        self.total_bytes = 0

    def consume(self, byte_count: int) -> None:
        if byte_count <= 0:
            return
        self.total_bytes += byte_count
        if self.bytes_per_second is None:
            return
        remaining = byte_count
        while remaining > 0:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._last_refill = now
                self._tokens = min(float(self.bytes_per_second), self._tokens + elapsed * self.bytes_per_second)
                allowed = min(remaining, int(self._tokens))
                if allowed > 0:
                    self._tokens -= allowed
                    remaining -= allowed
                    continue
                wait_seconds = max((remaining - self._tokens) / self.bytes_per_second, 0.01)
            time.sleep(min(wait_seconds, 0.25))


class RequestPacer:
    def __init__(self, requests_per_second: float | None) -> None:
        self.requests_per_second = requests_per_second if requests_per_second and requests_per_second > 0 else None
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def wait(self) -> None:
        if self.requests_per_second is None:
            return
        interval = 1.0 / self.requests_per_second
        with self._lock:
            now = time.monotonic()
            wait_seconds = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + interval
        if wait_seconds > 0:
            time.sleep(wait_seconds)
