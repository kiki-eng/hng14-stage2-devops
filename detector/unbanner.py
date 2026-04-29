"""
Auto-Unbanner — background thread that periodically checks for expired bans
and removes them.  Sends a Slack notification on every unban.
"""

import time
import threading
import logging
from typing import Optional

from blocker import IPBlocker

logger = logging.getLogger("detector.unbanner")


class AutoUnbanner:
    """Checks every 30 seconds for expired bans and releases them."""

    def __init__(self, blocker: IPBlocker, notify_callback=None,
                 check_interval: int = 30):
        self.blocker = blocker
        self.notify_callback = notify_callback
        self.check_interval = check_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.total_unbans = 0

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="unbanner")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        while not self._stop.is_set():
            self._check_expired()
            self._stop.wait(self.check_interval)

    def _check_expired(self):
        expired = self.blocker.get_expired_bans()
        for ip in expired:
            banned_info = self.blocker.get_banned().get(ip, {})
            duration = banned_info.get("duration_minutes", 0)
            condition = banned_info.get("condition", "unknown")
            ban_count = banned_info.get("ban_count", 0)

            success = self.blocker.unban(ip, reason="auto-unban (timer expired)")
            if success:
                self.total_unbans += 1
                logger.info("Auto-unbanned %s after %dm (offense #%d)",
                            ip, duration, ban_count)

                if self.notify_callback:
                    self.notify_callback(
                        action="unban",
                        ip=ip,
                        condition=condition,
                        duration=f"{duration}m",
                        ban_count=ban_count,
                    )
