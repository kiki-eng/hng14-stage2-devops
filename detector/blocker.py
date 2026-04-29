"""
IP Blocker — manages iptables DROP rules for per-IP anomalies.  Tracks ban
counts per IP to implement the escalating backoff schedule:
  1st offense → 10 minutes
  2nd offense → 30 minutes
  3rd offense → 2 hours
  4th+ offense → permanent
"""

import subprocess
import time
import threading
import logging
from typing import Optional

logger = logging.getLogger("detector.blocker")


class IPBlocker:
    """Thread-safe iptables manager with escalating ban durations."""

    def __init__(self, backoff_minutes: list[int], whitelist: list[str],
                 audit_callback=None):
        self.backoff_minutes = backoff_minutes  # e.g. [10, 30, 120]
        self.whitelist = set(whitelist)
        self.audit_callback = audit_callback

        self._lock = threading.Lock()

        # ip → {ban_time, duration_minutes, ban_count, condition, unban_time}
        self._banned: dict[str, dict] = {}
        # ip → cumulative offense count (persists across unbans)
        self._offense_count: dict[str, int] = {}

    # -- public API ----------------------------------------------------------

    def ban(self, ip: str, condition: str) -> Optional[dict]:
        """
        Ban an IP.  Returns the ban record or None if the IP is whitelisted
        or already banned.
        """
        if ip in self.whitelist:
            logger.info("Skipping whitelisted IP %s", ip)
            return None

        with self._lock:
            if ip in self._banned:
                return None  # already banned

            offense = self._offense_count.get(ip, 0)
            self._offense_count[ip] = offense + 1

            if offense < len(self.backoff_minutes):
                duration = self.backoff_minutes[offense]
            else:
                duration = 0  # permanent

            now = time.time()
            record = {
                "ip": ip,
                "ban_time": now,
                "duration_minutes": duration,
                "unban_time": now + duration * 60 if duration > 0 else None,
                "ban_count": offense + 1,
                "condition": condition,
            }
            self._banned[ip] = record

        # Execute iptables rule
        self._iptables_drop(ip)

        dur_str = f"{duration}m" if duration > 0 else "permanent"
        logger.info("BANNED %s for %s — %s", ip, dur_str, condition)

        if self.audit_callback:
            self.audit_callback(
                "BAN", ip, condition,
                rate="", baseline="", duration=dur_str
            )

        return record

    def unban(self, ip: str, reason: str = "auto-unban") -> bool:
        """Remove an IP ban and delete the iptables rule."""
        with self._lock:
            if ip not in self._banned:
                return False
            record = self._banned.pop(ip)

        self._iptables_remove(ip)
        logger.info("UNBANNED %s — %s", ip, reason)

        if self.audit_callback:
            dur = record.get("duration_minutes", 0)
            dur_str = f"{dur}m" if dur > 0 else "permanent"
            self.audit_callback(
                "UNBAN", ip, reason,
                rate="", baseline="", duration=dur_str
            )

        return True

    def get_banned(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._banned)

    def get_expired_bans(self) -> list[str]:
        """Return IPs whose ban duration has expired."""
        now = time.time()
        expired = []
        with self._lock:
            for ip, rec in self._banned.items():
                unban_time = rec.get("unban_time")
                if unban_time and now >= unban_time:
                    expired.append(ip)
        return expired

    # -- iptables helpers ----------------------------------------------------

    @staticmethod
    def _iptables_drop(ip: str):
        try:
            subprocess.run(
                ["iptables", "-A", "INPUT", "-s", ip, "-j", "DROP"],
                check=True, capture_output=True, timeout=10,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.error("iptables DROP failed for %s: %s", ip, exc)

    @staticmethod
    def _iptables_remove(ip: str):
        try:
            subprocess.run(
                ["iptables", "-D", "INPUT", "-s", ip, "-j", "DROP"],
                check=True, capture_output=True, timeout=10,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning("iptables DELETE failed for %s: %s", ip, exc)
