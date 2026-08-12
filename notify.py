#!/usr/bin/env python3
"""Phone notifications — Bark (iPhone) or ntfy.

Lifted from wn-monitor-web unchanged in behaviour, including the tiers that
were tuned by hand: nothing is ever "critical", there is no 30-second ring,
and pushes are grouped so iOS stacks them. Those were deliberate choices, not
defaults, so they are preserved rather than rethought.
"""

import json
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

import requests

HTTP = requests.Session()          # connection reuse across notifications
PROJECT_DIR = Path(__file__).resolve().parent
SEND_LOG_PATH = PROJECT_DIR / "notifications.log"

# Windows consoles and files default to the legacy code page, which cannot
# encode the emoji in these titles — that crashed the old radar mid-send.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def audit_send(backend: str, priority: str, title: str, outcome: str) -> None:
    """One line per attempt. Never raises: a failure to write the receipt must
    not be reported as a failed send, or the caller retries a push the phone
    already has."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(SEND_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{stamp} | {backend} | {priority} | {outcome} | {title}\n")
    except (OSError, UnicodeError):
        pass


class NtfyNotifier:
    # JSON publish endpoint rather than headers: HTTP headers are latin-1
    # only, which breaks emoji in titles.
    PRIORITY_TO_INT = {"min": 1, "low": 2, "default": 3, "high": 4,
                       "urgent": 5, "max": 5}

    def __init__(self, config: dict):
        self.server = config.get("ntfy_server", "https://ntfy.sh").rstrip("/")
        self.topic = config.get("ntfy_topic", "")
        if not self.topic or "CHANGE-ME" in self.topic:
            sys.exit("Set ntfy_topic in config.json to a long random string "
                     "first — topics are public.")

    def send(self, title, message, click_url, priority="high", group=None):
        payload = {"topic": self.topic, "title": title, "message": message,
                   "click": click_url,
                   "priority": self.PRIORITY_TO_INT.get(priority, 4)}
        try:
            resp = HTTP.post(self.server, json=payload, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            audit_send("ntfy", priority, title, f"FAILED {exc.__class__.__name__}")
            raise
        audit_send("ntfy", priority, title, "OK")


class BarkNotifier:
    # max maps to timeSensitive, not critical: critical breaks through silent
    # mode WITH sound, which was explicitly not wanted. timeSensitive buzzes.
    PRIORITY_TO_LEVEL = {"max": "timeSensitive", "urgent": "timeSensitive",
                         "high": "timeSensitive", "default": "active",
                         "low": "passive", "min": "passive"}

    def __init__(self, config: dict):
        self.key = config.get("bark_key", "")
        if not self.key:
            sys.exit('notifier is "bark" but bark_key is empty in config.json.')

    def send(self, title, message, click_url, priority="high", group=None):
        url = "https://api.day.app/{}/{}/{}".format(
            self.key, urllib.parse.quote(title, safe=""),
            urllib.parse.quote(message, safe=""))
        params = {"url": click_url,
                  "level": self.PRIORITY_TO_LEVEL.get(priority, "active")}
        if group:
            params["group"] = group        # stacks per category on iOS
        try:
            resp = HTTP.get(url, params=params, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            audit_send("bark", priority, title, f"FAILED {exc.__class__.__name__}")
            raise
        audit_send("bark", priority, title, "OK")


def make_notifier(config: dict):
    backend = config.get("notifier", "bark")
    if backend == "bark":
        return BarkNotifier(config)
    if backend == "ntfy":
        return NtfyNotifier(config)
    sys.exit(f'Unknown notifier "{backend}" (expected "bark" or "ntfy").')
