#!/usr/bin/env python3
"""The Chrome the watcher attaches to — finding it, starting it, checking it.

Used by watch.py, which starts this browser itself if it isn't already up, so
there is only ever one command to run. This browser is yours: you open the
streams, you close them, and nothing else ever navigates it.

Two things it must get right, both measured 2026-08-12:

  * It must NOT pass --enable-automation. That flag, not the debug port and
    not the CDP connection, is what sets navigator.webdriver. Launched this
    way the page reports false.

  * It MUST pass the three keep-alive flags. Chrome throttles background tabs
    to roughly one timer tick a minute after five minutes, and Whatnot's
    channel heartbeat is timer-driven — so without them a background tab can
    silently stop counting as present, which is the entire point of holding
    the tab open. Adding them does not re-set navigator.webdriver; that was
    checked.
"""

import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.json"
PROFILE_DIR = PROJECT_DIR / "chrome-profile"

KEEP_ALIVE_FLAGS = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
    "/opt/google/chrome/google-chrome",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_chrome():
    for path in CHROME_CANDIDATES:
        if Path(path).is_file():
            return str(path)
    for name in ("google-chrome", "google-chrome-stable", "chrome"):
        if (found := shutil.which(name)):
            return found
    return None


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        example = json.loads(
            (PROJECT_DIR / "config.example.json").read_text(encoding="utf-8"))
        CONFIG_PATH.write_text(json.dumps(example, indent=2), encoding="utf-8")
        print(f"  Created {CONFIG_PATH.name} — put your Bark key in it.")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def build_command(chrome: str, port: int, profile: Path) -> list:
    return [chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            *KEEP_ALIVE_FLAGS,
            "--no-first-run", "--no-default-browser-check",
            "https://www.whatnot.com/"]


def verify(port: int) -> list:
    """Confirm the two properties this design depends on. Returns problems.

    Asserted rather than assumed, because both fail silently: a throttled tab
    looks identical to a working one until you notice you stopped winning.
    """
    problems = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ["playwright is not installed — run: pip install -r requirements.txt"]
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if page.evaluate("navigator.webdriver"):
                problems.append(
                    "navigator.webdriver is TRUE — this browser is announcing "
                    "itself as automated. Something passed --enable-automation.")

            # Read the flags Chrome was actually started with, rather than
            # trying to observe throttling. Two earlier attempts were worse:
            # timing an interval on the startup tab false-positived (the
            # navigation to whatnot.com destroys the interval mid-count), and
            # timing one on a scratch tab proves nothing at all, because
            # throttling only applies to BACKGROUND tabs after several
            # minutes. chrome://version lists the real command line, so ask it.
            probe = ctx.new_page()
            try:
                probe.goto("chrome://version")
                probe.wait_for_timeout(600)
                cmdline = probe.evaluate("document.body.innerText") or ""
            except Exception:
                cmdline = ""
            finally:
                probe.close()

            if not cmdline:
                problems.append("could not read chrome://version to confirm "
                                "the keep-alive flags")
            else:
                missing = [f for f in KEEP_ALIVE_FLAGS if f not in cmdline]
                if missing:
                    problems.append(
                        "this Chrome was started without "
                        + ", ".join(missing) +
                        " — background tabs will be throttled after a few "
                        "minutes and may stop holding your giveaway entries. "
                        "Quit Chrome and let this start it.")
                if "enable-automation" in cmdline:
                    problems.append(
                        "--enable-automation is set — that is what makes "
                        "navigator.webdriver true.")
            browser.close()
    except Exception as exc:
        problems.append(f"could not attach to check ({exc.__class__.__name__})")
    return problems


def ensure_running(cfg: dict) -> bool:
    """Start the browser unless it is already up. Returns True if usable.

    Safe to call every time: an already-running browser is left completely
    alone, so restarting the watcher never disturbs the tabs you have open.
    """
    port = int(cfg.get("debug_port", 9222))

    if port_open(port):
        print(f"  Chrome already running on port {port} — leaving it alone.")
        return True

    chrome = find_chrome()
    if not chrome:
        print("  Google Chrome not found. Install it, or edit "
              "CHROME_CANDIDATES in chrome.py.")
        return False

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n  Starting Chrome on port {port}")
    print(f"  Profile: {PROFILE_DIR}")
    subprocess.Popen(build_command(chrome, port, PROFILE_DIR),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(30):
        if port_open(port):
            break
        time.sleep(0.5)
    else:
        print("  Chrome did not open its debug port. Is another Chrome already "
              "using this profile?")
        return False

    print("  Checking it looks like an ordinary browser...")
    problems = verify(port)
    if problems:
        print("\n  PROBLEMS:")
        for p in problems:
            print(f"    - {p}")
        print("\n  Fix these before relying on it.\n")
    else:
        print("    webdriver flag absent, keep-alive flags present — good.\n")

    print("  This browser is yours. First time: log in to Whatnot.")
    print("  Open the streams you want, plus optionally the purchases tab:")
    print("    https://www.whatnot.com/?activityTab=purchases\n")
    return True


if __name__ == "__main__":
    ensure_running(load_config())
