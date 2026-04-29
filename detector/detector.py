"""
Anomaly Detector — evaluates per-IP and global request rates against the
rolling baseline using z-score and rate-multiplier tests.  Also detects error
surges and tightens thresholds for misbehaving IPs.
"""

import time
import threading
from collections import deque
from typing import Optional

from baseline import RollingBaseline


class AnomalyResult:
    """Describes a single anomaly detection event."""

    def __init__(self, scope: str, ip: Optional[str], condition: str,
                 rate: float, baseline_mean: float, baseline_stddev: float,
                 zscore: float):
        self.scope = scope            # "per_ip" or "global"
        self.ip = ip
        self.condition = condition    # human-readable trigger description
        self.rate = rate
        self.baseline_mean = baseline_mean
        self.baseline_stddev = baseline_stddev
        self.zscore = zscore
        self.timestamp = time.time()


class SlidingWindow:
    """
    Deque-based sliding window that holds timestamps of events over the last
    `window_seconds` seconds.  Eviction happens on every insert and on rate
    queries so the window is always fresh.
    """

    def __init__(self, window_seconds: int = 60):
        self.window_seconds = window_seconds
        self._deque: deque = deque()
        self._lock = threading.Lock()

    def add(self, ts: Optional[float] = None):
        ts = ts or time.time()
        with self._lock:
            self._deque.append(ts)
            self._evict(ts)

    def rate(self) -> float:
        """Requests per second over the window."""
        now = time.time()
        with self._lock:
            self._evict(now)
            n = len(self._deque)
        return n / self.window_seconds if self.window_seconds else 0.0

    def count(self) -> int:
        now = time.time()
        with self._lock:
            self._evict(now)
            return len(self._deque)

    def _evict(self, now: float):
        cutoff = now - self.window_seconds
        while self._deque and self._deque[0] < cutoff:
            self._deque.popleft()


class AnomalyDetector:
    """
    Maintains per-IP and global sliding windows.  Call `process_event` for
    every parsed log line; the method returns an AnomalyResult if the event
    triggers detection, otherwise None.
    """

    def __init__(self, baseline: RollingBaseline,
                 window_seconds: int = 60,
                 zscore_threshold: float = 3.0,
                 rate_multiplier: float = 5.0,
                 error_surge_multiplier: float = 3.0,
                 error_tightened_zscore: float = 2.0,
                 error_tightened_rate_multiplier: float = 3.0):
        self.baseline = baseline
        self.window_seconds = window_seconds
        self.zscore_threshold = zscore_threshold
        self.rate_multiplier = rate_multiplier
        self.error_surge_multiplier = error_surge_multiplier
        self.error_tightened_zscore = error_tightened_zscore
        self.error_tightened_rate_multiplier = error_tightened_rate_multiplier

        self._lock = threading.Lock()

        # Per-IP sliding windows
        self._ip_windows: dict[str, SlidingWindow] = {}
        self._ip_error_windows: dict[str, SlidingWindow] = {}

        # Global sliding windows
        self._global_window = SlidingWindow(window_seconds)
        self._global_error_window = SlidingWindow(window_seconds)

        # Per-second counters for baseline feeding
        self._current_second: int = 0
        self._second_count: int = 0
        self._second_error_count: int = 0

        # Track recently alerted IPs to avoid alert floods (cooldown in seconds)
        self._alert_cooldown: dict[str, float] = {}
        self._global_alert_cooldown: float = 0.0
        self.alert_cooldown_seconds = 30

    def process_event(self, event: dict) -> Optional[AnomalyResult]:
        """
        Process a single request event.  Returns an AnomalyResult if an
        anomaly is detected, else None.
        """
        now = time.time()
        ip = event["source_ip"]
        status = event["status"]
        is_error = status >= 400

        # Feed sliding windows
        with self._lock:
            if ip not in self._ip_windows:
                self._ip_windows[ip] = SlidingWindow(self.window_seconds)
                self._ip_error_windows[ip] = SlidingWindow(self.window_seconds)
            self._ip_windows[ip].add(now)
            if is_error:
                self._ip_error_windows[ip].add(now)

        self._global_window.add(now)
        if is_error:
            self._global_error_window.add(now)

        # Feed baseline per-second counter
        epoch_second = int(now)
        if epoch_second != self._current_second:
            if self._current_second > 0:
                self.baseline.record_second(
                    self._current_second, self._second_count,
                    self._second_error_count
                )
            self._current_second = epoch_second
            self._second_count = 0
            self._second_error_count = 0
        self._second_count += 1
        if is_error:
            self._second_error_count += 1

        # -- per-IP detection ------------------------------------------------
        ip_rate = self._ip_windows[ip].rate()
        result = self._check_anomaly(ip, ip_rate, now)
        if result:
            return result

        # -- global detection ------------------------------------------------
        global_rate = self._global_window.rate()
        result = self._check_global_anomaly(global_rate, now)
        return result

    def _check_anomaly(self, ip: str, rate: float,
                       now: float) -> Optional[AnomalyResult]:
        mean = self.baseline.effective_mean
        std = self.baseline.effective_stddev

        if std == 0 or mean == 0:
            return None

        # Check alert cooldown
        last_alert = self._alert_cooldown.get(ip, 0)
        if now - last_alert < self.alert_cooldown_seconds:
            return None

        # Determine if error surge tightens thresholds
        z_thresh = self.zscore_threshold
        r_thresh = self.rate_multiplier
        error_rate = self._ip_error_windows.get(ip, SlidingWindow(1)).rate()
        error_mean = self.baseline.effective_error_mean

        if error_mean > 0 and error_rate > self.error_surge_multiplier * error_mean:
            z_thresh = self.error_tightened_zscore
            r_thresh = self.error_tightened_rate_multiplier

        zscore = (rate - mean) / std if std > 0 else 0.0

        if zscore > z_thresh:
            self._alert_cooldown[ip] = now
            return AnomalyResult(
                scope="per_ip", ip=ip,
                condition=f"Z-score {zscore:.2f} > {z_thresh} (rate={rate:.1f} req/s)",
                rate=rate, baseline_mean=mean, baseline_stddev=std,
                zscore=zscore,
            )

        if mean > 0 and rate > r_thresh * mean:
            self._alert_cooldown[ip] = now
            return AnomalyResult(
                scope="per_ip", ip=ip,
                condition=f"Rate {rate:.1f} > {r_thresh}x baseline mean {mean:.1f}",
                rate=rate, baseline_mean=mean, baseline_stddev=std,
                zscore=zscore,
            )

        return None

    def _check_global_anomaly(self, rate: float,
                              now: float) -> Optional[AnomalyResult]:
        mean = self.baseline.effective_mean
        std = self.baseline.effective_stddev

        if std == 0 or mean == 0:
            return None

        if now - self._global_alert_cooldown < self.alert_cooldown_seconds:
            return None

        zscore = (rate - mean) / std if std > 0 else 0.0

        if zscore > self.zscore_threshold:
            self._global_alert_cooldown = now
            return AnomalyResult(
                scope="global", ip=None,
                condition=f"Global z-score {zscore:.2f} > {self.zscore_threshold} "
                          f"(rate={rate:.1f} req/s)",
                rate=rate, baseline_mean=mean, baseline_stddev=std,
                zscore=zscore,
            )

        if mean > 0 and rate > self.rate_multiplier * mean:
            self._global_alert_cooldown = now
            return AnomalyResult(
                scope="global", ip=None,
                condition=f"Global rate {rate:.1f} > {self.rate_multiplier}x "
                          f"baseline mean {mean:.1f}",
                rate=rate, baseline_mean=mean, baseline_stddev=std,
                zscore=zscore,
            )

        return None

    def get_ip_rates(self) -> dict[str, float]:
        """Return current req/s for all tracked IPs."""
        with self._lock:
            return {ip: w.rate() for ip, w in self._ip_windows.items()}

    def get_global_rate(self) -> float:
        return self._global_window.rate()

    def get_top_ips(self, n: int = 10) -> list[tuple[str, float]]:
        rates = self.get_ip_rates()
        return sorted(rates.items(), key=lambda x: x[1], reverse=True)[:n]
