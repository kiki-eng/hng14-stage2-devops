"""
Anomaly Detection Engine — main entry point.

Orchestrates all components:
  - LogMonitor:      tails Nginx JSON access log
  - RollingBaseline: computes adaptive mean/stddev
  - AnomalyDetector: z-score and rate-multiplier checks
  - IPBlocker:       iptables DROP management
  - AutoUnbanner:    scheduled ban release
  - SlackNotifier:   webhook alerts
  - Dashboard:       Flask live-metrics UI

Run as: python main.py
"""

import os
import sys
import time
import signal
import logging
import threading
import yaml

from monitor import LogMonitor
from baseline import RollingBaseline
from detector import AnomalyDetector
from blocker import IPBlocker
from unbanner import AutoUnbanner
from notifier import SlackNotifier
from dashboard import app as flask_app, init_dashboard

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("detector")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)

    # Environment variable overrides
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if slack_url:
        cfg["slack_webhook_url"] = slack_url
    elif cfg.get("slack_webhook_url", "").startswith("$"):
        cfg["slack_webhook_url"] = ""

    whitelist_env = os.environ.get("WHITELIST_IPS", "")
    if whitelist_env:
        cfg["whitelist_ips"] = [ip.strip() for ip in whitelist_env.split(",") if ip.strip()]

    return cfg

# ---------------------------------------------------------------------------
# Audit logger
# ---------------------------------------------------------------------------

_audit_lock = threading.Lock()
_audit_fh = None


def open_audit_log(path: str):
    global _audit_fh
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _audit_fh = open(path, "a", buffering=1)  # line-buffered


def write_audit(action: str, ip: str, condition: str,
                rate: str, baseline: str, duration: str):
    """
    [timestamp] ACTION ip | condition | rate | baseline | duration
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    line = f"[{ts}] {action} {ip} | {condition} | rate={rate} | baseline={baseline} | duration={duration}\n"
    with _audit_lock:
        if _audit_fh:
            _audit_fh.write(line)
            _audit_fh.flush()
    logger.info("AUDIT: %s", line.strip())

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = load_config()
    start_time = time.time()

    logger.info("=" * 60)
    logger.info("  HNG Anomaly Detection Engine starting")
    logger.info("=" * 60)
    logger.info("Log file:  %s", cfg["log_file"])
    logger.info("Audit log: %s", cfg["audit_log_file"])
    logger.info("Dashboard: http://%s:%d", cfg["dashboard_host"], cfg["dashboard_port"])

    # Audit log
    open_audit_log(cfg["audit_log_file"])

    # Components
    baseline = RollingBaseline(
        window_minutes=cfg["baseline_window_minutes"],
        recalc_interval=cfg["baseline_recalc_interval_seconds"],
        min_samples=cfg["baseline_min_samples"],
        floor_mean=cfg["baseline_floor_mean"],
        floor_stddev=cfg["baseline_floor_stddev"],
    )

    detector_engine = AnomalyDetector(
        baseline=baseline,
        window_seconds=cfg["sliding_window_seconds"],
        zscore_threshold=cfg["zscore_threshold"],
        rate_multiplier=cfg["rate_multiplier_threshold"],
        error_surge_multiplier=cfg["error_surge_multiplier"],
        error_tightened_zscore=cfg["error_tightened_zscore"],
        error_tightened_rate_multiplier=cfg["error_tightened_rate_multiplier"],
    )

    notifier = SlackNotifier(
        webhook_url=cfg.get("slack_webhook_url", ""),
        enabled=cfg.get("slack_enabled", True),
    )

    blocker = IPBlocker(
        backoff_minutes=cfg["ban_backoff_minutes"],
        whitelist=cfg.get("whitelist_ips", []),
        audit_callback=write_audit,
    )

    def on_unban(action, ip, condition, duration, ban_count):
        notifier.send_unban_alert(ip, condition, duration, ban_count)

    unbanner = AutoUnbanner(
        blocker=blocker,
        notify_callback=on_unban,
        check_interval=30,
    )

    # Event handler — called for every parsed log line
    def on_event(event: dict):
        result = detector_engine.process_event(event)
        if result is None:
            return

        if result.scope == "per_ip":
            ban_record = blocker.ban(result.ip, result.condition)
            if ban_record:
                dur = ban_record["duration_minutes"]
                dur_str = f"{dur}m" if dur > 0 else "permanent"
                notifier.send_ban_alert(
                    ip=result.ip,
                    condition=result.condition,
                    rate=result.rate,
                    baseline_mean=result.baseline_mean,
                    baseline_stddev=result.baseline_stddev,
                    duration=dur_str,
                    ban_count=ban_record["ban_count"],
                )

        elif result.scope == "global":
            notifier.send_global_alert(
                condition=result.condition,
                rate=result.rate,
                baseline_mean=result.baseline_mean,
                baseline_stddev=result.baseline_stddev,
            )
            write_audit(
                "GLOBAL_ANOMALY", "—", result.condition,
                rate=f"{result.rate:.1f}",
                baseline=f"{result.baseline_mean:.2f}",
                duration="—",
            )

    log_monitor = LogMonitor(
        log_path=cfg["log_file"],
        callback=on_event,
        poll_interval=0.1,
    )

    # Baseline recalculation audit hook
    _orig_recalc = baseline._recalculate
    def _audited_recalc():
        _orig_recalc()
        write_audit(
            "BASELINE_RECALC", "—",
            f"mean={baseline.effective_mean:.2f} stddev={baseline.effective_stddev:.2f}",
            rate="—",
            baseline=f"{baseline.effective_mean:.2f}±{baseline.effective_stddev:.2f}",
            duration="—",
        )
    baseline._recalculate = _audited_recalc

    # Initialize dashboard
    init_dashboard(detector_engine, baseline, blocker, log_monitor, start_time)

    # Wire up notifier stats for dashboard
    flask_app.config["_state"] = {
        "notifier": notifier,
    }

    import dashboard as dash_mod
    dash_mod._state["notifier_msgs"] = 0
    dash_mod._state["notifier_errs"] = 0

    def _update_notifier_stats():
        while True:
            dash_mod._state["notifier_msgs"] = notifier.messages_sent
            dash_mod._state["notifier_errs"] = notifier.errors
            time.sleep(1)

    threading.Thread(target=_update_notifier_stats, daemon=True).start()

    # Start all components
    baseline.start()
    log_monitor.start()
    unbanner.start()

    logger.info("All components started. Monitoring %s", cfg["log_file"])

    # Graceful shutdown
    shutdown = threading.Event()

    def handle_signal(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        shutdown.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Run Flask in a thread
    flask_thread = threading.Thread(
        target=lambda: flask_app.run(
            host=cfg["dashboard_host"],
            port=cfg["dashboard_port"],
            debug=False,
            use_reloader=False,
        ),
        daemon=True,
        name="dashboard",
    )
    flask_thread.start()
    logger.info("Dashboard serving on http://%s:%d",
                cfg["dashboard_host"], cfg["dashboard_port"])

    # Block main thread until shutdown
    try:
        shutdown.wait()
    except KeyboardInterrupt:
        pass

    logger.info("Shutting down...")
    log_monitor.stop()
    baseline.stop()
    unbanner.stop()

    if _audit_fh:
        _audit_fh.close()

    logger.info("Goodbye.")


if __name__ == "__main__":
    main()
