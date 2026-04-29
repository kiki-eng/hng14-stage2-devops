"""
Rolling Baseline — tracks per-second request counts over a 30-minute window,
recomputes mean and standard deviation every 60 seconds, and maintains per-hour
slots so the effective baseline reflects the current traffic pattern.
"""

import math
import time
import threading
from collections import deque
from typing import Optional


class HourlySlot:
    """Accumulates per-second counts for a single clock-hour."""

    def __init__(self):
        self.counts: deque = deque()  # (epoch_second, count)
        self.total = 0
        self.sum_sq = 0.0
        self.n = 0

    def add(self, count: int):
        self.total += count
        self.sum_sq += count * count
        self.n += 1

    @property
    def mean(self) -> float:
        return self.total / self.n if self.n else 0.0

    @property
    def stddev(self) -> float:
        if self.n < 2:
            return 0.0
        variance = (self.sum_sq / self.n) - (self.mean ** 2)
        return math.sqrt(max(variance, 0.0))


class RollingBaseline:
    """
    Maintains a deque of (epoch_second, request_count) pairs covering the last
    `window_minutes` minutes.  Every `recalc_interval` seconds the effective
    mean and stddev are recomputed.  Per-hour slots are kept; the current hour's
    slot is preferred when it has enough data.
    """

    def __init__(self, window_minutes: int = 30,
                 recalc_interval: int = 60,
                 min_samples: int = 30,
                 floor_mean: float = 2.0,
                 floor_stddev: float = 1.0):
        self.window_seconds = window_minutes * 60
        self.recalc_interval = recalc_interval
        self.min_samples = min_samples
        self.floor_mean = floor_mean
        self.floor_stddev = floor_stddev

        self._lock = threading.Lock()

        # (epoch_second, count) – one entry per second that had traffic
        self._counts: deque = deque()
        # Per-hour accumulators keyed by hour-of-day (0-23)
        self._hourly: dict[int, HourlySlot] = {}

        # Published baseline values
        self.effective_mean: float = floor_mean
        self.effective_stddev: float = floor_stddev
        self.effective_error_mean: float = 0.0
        self.last_recalc: float = 0.0

        # Error baseline tracking
        self._error_counts: deque = deque()

        # History for graphing
        self.history: deque = deque(maxlen=360)  # up to 6 hours at 1-min intervals

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- recording -----------------------------------------------------------

    def record_second(self, epoch_second: int, count: int, error_count: int = 0):
        """Called once per second with the total request count for that second."""
        with self._lock:
            self._counts.append((epoch_second, count))
            self._error_counts.append((epoch_second, error_count))

            hour = time.localtime(epoch_second).tm_hour
            if hour not in self._hourly:
                self._hourly[hour] = HourlySlot()
            self._hourly[hour].add(count)

    # -- recalculation -------------------------------------------------------

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="baseline-recalc")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        while not self._stop.is_set():
            self._recalculate()
            self._stop.wait(self.recalc_interval)

    def _recalculate(self):
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            # Evict stale entries
            while self._counts and self._counts[0][0] < cutoff:
                self._counts.popleft()
            while self._error_counts and self._error_counts[0][0] < cutoff:
                self._error_counts.popleft()

            values = [c for _, c in self._counts]
            error_values = [c for _, c in self._error_counts]

        n = len(values)
        if n >= self.min_samples:
            # Try current hour's slot first
            current_hour = time.localtime(now).tm_hour
            slot = self._hourly.get(current_hour)
            if slot and slot.n >= self.min_samples:
                raw_mean = slot.mean
                raw_std = slot.stddev
            else:
                raw_mean = sum(values) / n
                variance = sum((v - raw_mean) ** 2 for v in values) / n
                raw_std = math.sqrt(max(variance, 0.0))

            self.effective_mean = max(raw_mean, self.floor_mean)
            self.effective_stddev = max(raw_std, self.floor_stddev)

            # Error baseline
            if error_values:
                err_mean = sum(error_values) / len(error_values)
                self.effective_error_mean = err_mean
        else:
            self.effective_mean = max(self.effective_mean, self.floor_mean)
            self.effective_stddev = max(self.effective_stddev, self.floor_stddev)

        self.last_recalc = now

        self.history.append({
            "timestamp": now,
            "effective_mean": self.effective_mean,
            "effective_stddev": self.effective_stddev,
            "sample_count": n,
            "hour": time.localtime(now).tm_hour,
        })

    def get_hourly_stats(self) -> dict:
        """Return per-hour baseline stats for dashboard display."""
        result = {}
        for hour, slot in self._hourly.items():
            result[hour] = {
                "mean": round(slot.mean, 2),
                "stddev": round(slot.stddev, 2),
                "samples": slot.n,
            }
        return result
