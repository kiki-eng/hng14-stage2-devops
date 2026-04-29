"""
Slack Notifier — sends formatted webhook messages for ban, unban, and global
anomaly events.  Each alert includes the condition that fired, current rate,
baseline values, timestamp, and ban duration where applicable.
"""

import json
import time
import logging
import threading
import requests
from typing import Optional

logger = logging.getLogger("detector.notifier")


class SlackNotifier:
    """Non-blocking Slack webhook sender."""

    def __init__(self, webhook_url: str, enabled: bool = True):
        self.webhook_url = webhook_url
        self.enabled = enabled and bool(webhook_url)
        self.messages_sent = 0
        self.errors = 0

    def send_ban_alert(self, ip: str, condition: str, rate: float,
                       baseline_mean: float, baseline_stddev: float,
                       duration: str, ban_count: int):
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🚨 IP BANNED", "emoji": True}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*IP:*\n`{ip}`"},
                    {"type": "mrkdwn", "text": f"*Ban Duration:*\n{duration}"},
                    {"type": "mrkdwn", "text": f"*Offense #:*\n{ban_count}"},
                    {"type": "mrkdwn", "text": f"*Current Rate:*\n{rate:.1f} req/s"},
                    {"type": "mrkdwn", "text": f"*Baseline Mean:*\n{baseline_mean:.2f} req/s"},
                    {"type": "mrkdwn", "text": f"*Baseline StdDev:*\n{baseline_stddev:.2f}"},
                ]
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Condition:*\n{condition}"}
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn",
                     "text": f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"}
                ]
            }
        ]
        self._send({"blocks": blocks})

    def send_unban_alert(self, ip: str, condition: str, duration: str,
                         ban_count: int):
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "✅ IP UNBANNED", "emoji": True}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*IP:*\n`{ip}`"},
                    {"type": "mrkdwn", "text": f"*Was Banned For:*\n{duration}"},
                    {"type": "mrkdwn", "text": f"*Total Offenses:*\n{ban_count}"},
                    {"type": "mrkdwn", "text": f"*Reason:*\nauto-unban (timer expired)"},
                ]
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn",
                     "text": f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"}
                ]
            }
        ]
        self._send({"blocks": blocks})

    def send_global_alert(self, condition: str, rate: float,
                          baseline_mean: float, baseline_stddev: float):
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "⚠️ GLOBAL ANOMALY DETECTED",
                         "emoji": True}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Global Rate:*\n{rate:.1f} req/s"},
                    {"type": "mrkdwn", "text": f"*Baseline Mean:*\n{baseline_mean:.2f} req/s"},
                    {"type": "mrkdwn", "text": f"*Baseline StdDev:*\n{baseline_stddev:.2f}"},
                ]
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Condition:*\n{condition}"}
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn",
                     "text": f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"}
                ]
            }
        ]
        self._send({"blocks": blocks})

    def _send(self, payload: dict):
        if not self.enabled:
            logger.debug("Slack disabled; would have sent: %s",
                         json.dumps(payload, indent=2))
            return

        def _do_send():
            try:
                resp = requests.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    self.messages_sent += 1
                else:
                    self.errors += 1
                    logger.error("Slack returned %d: %s",
                                 resp.status_code, resp.text[:200])
            except Exception as exc:
                self.errors += 1
                logger.error("Slack send failed: %s", exc)

        threading.Thread(target=_do_send, daemon=True).start()
