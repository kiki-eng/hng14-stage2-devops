"""
Log Monitor — continuously tails the Nginx JSON access log and parses each
line into a structured request event.  Uses inotify-style polling so it picks
up new lines as soon as Nginx flushes them, and handles log rotation.
"""

import json
import os
import time
import threading
from collections import deque
from typing import Callable, Optional


class LogMonitor:
    """Tail a file and emit parsed JSON request dicts to a callback."""

    def __init__(self, log_path: str, callback: Callable[[dict], None],
                 poll_interval: float = 0.1):
        self.log_path = log_path
        self.callback = callback
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lines_processed = 0
        self.parse_errors = 0

    # -- public API ----------------------------------------------------------

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="log-monitor")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    # -- internal ------------------------------------------------------------

    def _wait_for_file(self):
        """Block until the log file appears on disk."""
        while not self._stop_event.is_set():
            if os.path.exists(self.log_path):
                return
            time.sleep(1)

    def _run(self):
        self._wait_for_file()
        fh = open(self.log_path, "r")
        fh.seek(0, os.SEEK_END)
        inode = os.fstat(fh.fileno()).st_ino

        try:
            while not self._stop_event.is_set():
                line = fh.readline()
                if line:
                    self._handle_line(line.strip())
                    continue

                # No new data — check for log rotation
                try:
                    current_inode = os.stat(self.log_path).st_ino
                except FileNotFoundError:
                    time.sleep(self.poll_interval)
                    continue

                if current_inode != inode:
                    fh.close()
                    try:
                        fh = open(self.log_path, "r")
                    except FileNotFoundError:
                        time.sleep(self.poll_interval)
                        continue
                    inode = current_inode
                else:
                    time.sleep(self.poll_interval)
        finally:
            fh.close()

    def _handle_line(self, raw: str):
        if not raw:
            return
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            self.parse_errors += 1
            return

        event = {
            "source_ip":     entry.get("source_ip", "unknown"),
            "timestamp":     entry.get("timestamp", ""),
            "method":        entry.get("method", ""),
            "path":          entry.get("path", ""),
            "status":        int(entry.get("status", 0)),
            "response_size": int(entry.get("response_size", 0)),
            "raw":           entry,
        }
        self.lines_processed += 1
        self.callback(event)
