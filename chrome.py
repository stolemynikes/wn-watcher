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
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.json"
PROFILE_DIR = PROJECT_DIR / "chrome-profile"


def accounts(cfg: dict) -> list:
    """The configured accounts, oldest-style config migrated on the fly.

    One Whatnot login lives in one Chrome profile, so switching account =
    switching profile. A config without an `accounts` list is read as a single
    account built from the flat `my_username` + the original profile, so an
    upgrade changes nothing until a second account is actually added.
    """
    accs = cfg.get("accounts")
    if isinstance(accs, list) and accs:
        return accs
    return [{"label": "Account 1", "profile": "chrome-profile",
             "username": str(cfg.get("my_username", "")).strip()}]


def active_account(cfg: dict) -> dict:
    """Whichever account is switched on right now. Falls back to the first."""
    accs = accounts(cfg)
    idx = int(cfg.get("active_account", 0))
    if not 0 <= idx < len(accs):
        idx = 0
    return accs[idx]


def profile_dir_for(cfg: dict) -> Path:
    """Filesystem path of the active account's Chrome profile."""
    name = active_account(cfg).get("profile") or "chrome-profile"
    p = Path(name)
    return p if p.is_absolute() else PROJECT_DIR / p

KEEP_ALIVE_FLAGS = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]


def chrome_candidates():
    """Where Chrome actually lives, built at call time.

    Windows needs more than two hardcoded paths. Installing Chrome without
    admin rights — which is what happens on a locked-down or work machine —
    puts it under LOCALAPPDATA and nowhere near Program Files. And Program
    Files itself is not always on C:, so it is read from the environment
    rather than assumed. This is the same trap that made the radar think
    Tailscale was not installed.
    """
    out = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
           "/opt/google/chrome/google-chrome",
           "/snap/bin/chromium", "/usr/bin/chromium-browser"]
    suffix = Path("Google") / "Chrome" / "Application" / "chrome.exe"
    for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if (root := os.environ.get(var)):
            out.append(str(Path(root) / suffix))
    return out


def find_chrome():
    for path in chrome_candidates():
        try:
            if Path(path).is_file():
                return str(path)
        except OSError:
            continue
    for name in ("google-chrome", "google-chrome-stable", "chrome", "chrome.exe"):
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


def build_command(chrome: str, port: int, profile: Path,
                  mute: bool = True) -> list:
    """The launch line. Note what is NOT here: --enable-automation.

    --mute-audio is safe to add. Measured 2026-08-12: with it set, a page still
    reports video.muted false, its volume unchanged and AudioContext running —
    the mute happens in Chrome's audio pipeline, below anything JavaScript can
    observe. Six live streams at once is otherwise unbearable.
    """
    return [chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            *KEEP_ALIVE_FLAGS,
            *(["--mute-audio"] if mute else []),
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


def radar_chromes(port: int) -> list:
    """Every Chrome this project launched on the debug port, as
    (pid, profile_dir). Normally one; more than one is the fault this exists
    to expose.

    Identity here is the profile, not the port. Two Chromes can each hold
    "port 9222" without conflicting — one binds 127.0.0.1, the other [::1] —
    and then port_open() answers about whichever took IPv4 while a link opened
    by profile lands in the other. The watcher reads one browser, your tabs
    open in the other, and both look healthy.
    """
    try:
        import psutil
    except ImportError:
        return []
    out = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        argv = proc.info.get("cmdline") or []
        line = " ".join(argv)
        if f"--remote-debugging-port={port}" not in line:
            continue
        if "--type=" in line:            # renderer/gpu helper, not the browser
            continue
        for arg in argv:
            if arg.startswith("--user-data-dir="):
                profile = Path(arg.split("=", 1)[1])
                if PROJECT_DIR in profile.parents or profile == PROJECT_DIR:
                    out.append((proc.info["pid"], profile))
                break
    return out


def ensure_running(cfg: dict) -> bool:
    """Start the browser unless it is already up. Returns True if usable.

    Safe to call every time: an already-running browser is left completely
    alone, so restarting the watcher never disturbs the tabs you have open.
    """
    port = int(cfg.get("debug_port", 9222))
    wanted = profile_dir_for(cfg)

    if port_open(port):
        running = radar_chromes(port)
        # Leaving it alone is only right when it is the browser we mean. A
        # leftover from another account answers the port just as readily, and
        # then the watcher reads that one while every link opens in the
        # account's own profile — each half working, the pair useless.
        if len(running) == 1 and running[0][1] == wanted:
            print(f"  Chrome already running on port {port} — leaving it alone.")
            return True
        if running:
            others = ", ".join(sorted({p.name for _, p in running
                                       if p != wanted}))
            print(f"  Chrome on port {port} is running the wrong profile"
                  f"{f' ({others})' if others else ''} — restarting it on "
                  f"{wanted.name}.")
        else:
            print(f"  Something else is on port {port} — not ours; "
                  f"starting {wanted.name} would collide.")
            return False
        stop_chrome(cfg)

    chrome = find_chrome()
    if not chrome:
        print("  Google Chrome not found. Install it from google.com/chrome, "
              "or add its path to chrome_candidates() in chrome.py.")
        return False

    profile = wanted
    profile.mkdir(parents=True, exist_ok=True)
    print(f"\n  Starting Chrome on port {port}")
    print(f"  Account: {active_account(cfg).get('label', '?')}  Profile: {profile}")
    subprocess.Popen(build_command(chrome, port, profile,
                                   bool(cfg.get("mute_audio", True))),
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


def stop_chrome(cfg: dict) -> str:
    """Quit the Chrome the radar launched, so a different profile can take the
    debug port. Only ours: it matches on both our debug port and a profile
    under this project, never a Chrome you opened for your own browsing.

    Switching account needs this — one profile at a time holds the port, so
    the old one must go before the new one starts.
    """
    try:
        import psutil
    except ImportError:
        return "psutil not installed — cannot stop Chrome"
    port = int(cfg.get("debug_port", 9222))
    targets = []
    for pid, _profile in radar_chromes(port):
        try:
            targets.append(psutil.Process(pid))
        except psutil.Error:
            pass
    if not targets:
        return "no radar Chrome was running"
    killed = len(targets)
    doomed = list(targets)
    for proc in targets:
        try:
            children = proc.children(recursive=True)
            doomed.extend(children)
            for child in children:
                child.terminate()
            proc.terminate()
        except psutil.Error:
            pass
    # SIGTERM is polite but slow, and Chrome does not always take it. Wait on
    # the processes themselves: the port going quiet is not the done signal it
    # looks like — Chrome closes the listening socket early in its shutdown and
    # can then abandon the quit, leaving the browser alive and the next launch
    # racing it for the port. That race is what puts two profiles on one port.
    _gone, alive = psutil.wait_procs(doomed, timeout=6)
    if not alive:
        return f"stopped Chrome ({killed} tree{'s' if killed != 1 else ''})"
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(alive, timeout=5)
    return (f"stopped Chrome ({killed} tree, forced)" if not alive
            else f"WARNING: {len(alive)} Chrome processes would not quit")


def open_visible(cfg: dict, url: str) -> bool:
    """Open a URL as a real, visible tab in the radar's Chrome — not a
    background CDP target (which has no window you can click). Relaunching the
    Chrome binary against the same profile routes the URL into the existing
    instance and opens a normal window/tab there.

    Used to drop the user onto a streamer's profile (where their live shows are
    one tap away) or a fresh search tab, in the account's own profile.
    """
    exe = find_chrome()
    if not exe:
        return False
    try:
        subprocess.Popen([exe, f"--user-data-dir={profile_dir_for(cfg)}", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def list_whatnot_tabs(cfg: dict) -> list:
    """Every Whatnot tab open in the radar's Chrome, over the debug HTTP
    endpoint (no Playwright, so it never collides with the watcher's own
    connection). Each entry: {id, url, title}."""
    import urllib.request
    port = int(cfg.get("debug_port", 9222))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json",
                                    timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    out = []
    for t in data if isinstance(data, list) else []:
        if t.get("type") == "page" and "whatnot.com" in (t.get("url") or ""):
            out.append({"id": t.get("id"), "url": t.get("url"),
                        "title": t.get("title", "")})
    return out


def _stream_id(url: str) -> str:
    """The /live/<id> segment of a Whatnot URL, ignoring query params, so two
    links to the same stream match even if one carries ?referringSource=…."""
    if "/live/" in (url or ""):
        return url.split("/live/")[1].split("?")[0]
    return ""


def close_tab_by_url(cfg: dict, url: str) -> str:
    """Close the one tab that matches this URL — same stream id, or the same
    URL outright. Lets a single stream be closed without touching the rest."""
    import urllib.request
    port = int(cfg.get("debug_port", 9222))
    want_id = _stream_id(url)
    n = 0
    for t in list_whatnot_tabs(cfg):
        same = (t["url"] == url or
                (want_id and _stream_id(t["url"]) == want_id))
        if same:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/close/{t['id']}", timeout=3)
                n += 1
            except Exception:
                pass
    return f"closed {n} tab{'s' if n != 1 else ''}"


def close_tabs(cfg: dict, only_live: bool = False) -> str:
    """Close Whatnot tabs in the radar's Chrome. only_live closes just the
    /live/ stream tabs and leaves feed/search tabs alone. Chrome, the watcher
    and the panel are untouched — this shuts tabs, not the browser."""
    import urllib.request
    port = int(cfg.get("debug_port", 9222))
    tabs = list_whatnot_tabs(cfg)
    if only_live:
        tabs = [t for t in tabs if "/live/" in (t["url"] or "")]
    n = 0
    for t in tabs:
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/close/{t['id']}", timeout=3)
            n += 1
        except Exception:
            pass
    return f"closed {n} tab{'s' if n != 1 else ''}"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "close-tabs":
        print(close_tabs(load_config(),
                         only_live="--live" in sys.argv))
    else:
        ensure_running(load_config())
