#!/usr/bin/env python3
"""Watch the Whatnot tabs YOU opened, and buzz your phone on a giveaway.

    python watch.py            # start the browser if needed, then watch
    python watch.py probe      # dump what it can see, and fix nothing
    python watch.py test       # send a test notification

One command. It starts the browser if it isn't already up, attaches, and then
waits — you open your streams whenever you like, before or after, and it picks
them up. Reading the page rather than the socket is what allows that. It never
navigates, opens, closes or reloads a tab. Entering a giveaway is always
yours to do by hand — that is Whatnot's rule and this tool does not bend it.

Why the DOM and not the WebSocket: a socket already open when we attach is
invisible to us — measured, zero frames, via both Playwright's event and raw
CDP. Reading the page has no such restriction, which is what lets you open
your tabs first and start this afterwards.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import chrome
import notify

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.json"
SELECTORS_PATH = PROJECT_DIR / "selectors.json"
STATE_PATH = PROJECT_DIR / "state.json"
BASE_URL = "https://www.whatnot.com"


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_json(path: Path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if fallback is None:
            sys.exit(f"{path.name} is missing or unreadable.")
        return fallback


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


# --- reading a page ---------------------------------------------------------
#
# One evaluate per tab per poll, returning a small struct. All matching is done
# in Python against the page's visible text, so the patterns live in
# selectors.json and a broken selector is a config edit, not a code change.

PAGE_TEXT = """() => ({
    url: location.href,
    title: document.title,
    text: (document.body && document.body.innerText || '').slice(0, 20000)
})"""

ROWS_JS = """(sel) => Array.from(document.querySelectorAll(sel))
    .map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim())
    .filter(Boolean)"""


def first_group(pattern, text, default=None):
    """Regex value extraction that tolerates a missing or broken pattern."""
    if not pattern:
        return default
    try:
        m = re.search(pattern, text, re.I | re.M)
    except re.error:
        return default
    if not m:
        return default
    return (m.group(1) if m.groups() else m.group(0)).strip()


def matches(pattern, text) -> bool:
    if not pattern:
        return False
    try:
        return bool(re.search(pattern, text, re.I | re.M))
    except re.error:
        return False


def read_live_tab(text: str, sel: dict) -> dict:
    """Turn a stream tab's visible text into a giveaway reading."""
    live = sel.get("live", {})
    if matches(live.get("challenge"), text):
        return {"challenge": True, "present": False}
    present = matches(live.get("giveaway_present"), text)
    entries = first_group(live.get("entries"), text)
    return {
        "challenge": False,
        "present": present,
        "prize": (first_group(live.get("prize"), text) or "") if present else "",
        "entries": int(entries) if (entries or "").isdigit() else None,
        "buyers_only": present and matches(live.get("buyers_only"), text),
        "followers_only": present and matches(live.get("followers_only"), text),
    }


def signature(url: str, reading: dict) -> str:
    """Identity of a giveaway, for deduping.

    The DOM carries no giveaway id, so a chained second giveaway is told from
    the first by its prize. Entry count is deliberately NOT part of this: it
    changes every few seconds and would make every poll a new giveaway.
    """
    return f"{url}|{reading.get('prize', '')}|{int(bool(reading.get('buyers_only')))}"


class TabTracker:
    """Per-tab state machine: absent -> present -> absent.

    Pure, so the interesting behaviour can be tested without a browser.
    `confirm` polls of agreement are required before a giveaway is announced,
    which rides out a half-rendered frame at no real cost against a five
    minute giveaway.
    """

    def __init__(self, confirm: int = 2):
        self.confirm = confirm
        self.current = None        # signature currently being reported
        self.candidate = None      # signature seen but not yet confirmed
        self.streak = 0
        self.absent_streak = 0

    def update(self, reading: dict, sig: str):
        """Feed one poll. Returns 'started' the moment a giveaway is confirmed,
        'ended' when one goes away, else None."""
        if not reading.get("present"):
            self.candidate, self.streak = None, 0
            self.absent_streak += 1
            if self.current and self.absent_streak >= self.confirm:
                self.current = None
                return "ended"
            return None
        self.absent_streak = 0
        if sig == self.current:
            return None                      # already announced
        if sig == self.candidate:
            self.streak += 1
        else:
            self.candidate, self.streak = sig, 1
        if self.streak >= self.confirm:
            self.current, self.candidate, self.streak = sig, None, 0
            return "started"
        return None


# --- attaching --------------------------------------------------------------


def attach(port: int):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    except Exception as exc:
        pw.stop()
        raise SystemExit(
            f"Could not attach to Chrome on port {port} "
            f"({exc.__class__.__name__}).")
    return pw, browser


def whatnot_pages(browser):
    """Every tab currently on Whatnot. Recomputed each poll, so tabs you open
    or close during a run are picked up without restarting."""
    out = []
    for ctx in browser.contexts:
        for page in ctx.pages:
            try:
                if "whatnot.com" in (page.url or ""):
                    out.append(page)
            except Exception:
                continue        # closed between listing and reading
    return out


# --- commands ---------------------------------------------------------------


def cmd_probe(cfg: dict, sel: dict) -> None:
    """Show what the watcher can see, and what the patterns currently match.

    This exists because the fragile part of this design is Whatnot's markup.
    When detection stops working, this output is the fix: read it, correct a
    regex in selectors.json, done.
    """
    pw, browser = attach(int(cfg.get("debug_port", 9222)))
    try:
        pages = whatnot_pages(browser)
        if not pages:
            print("\n  No Whatnot tabs are open in that browser.\n")
            return
        print(f"\n  {len(pages)} Whatnot tab(s) attached.\n")
        for page in pages:
            try:
                info = page.evaluate(PAGE_TEXT)
            except Exception as exc:
                print(f"  ! could not read a tab ({exc.__class__.__name__})")
                continue
            url, text = info["url"], info["text"]
            print("=" * 72)
            print(f"  {url}")
            print(f"  title: {info['title']}")
            is_purchases = matches(sel["purchases"].get("url_match"), url)
            if is_purchases:
                rows = page.evaluate(ROWS_JS, sel["purchases"]["row_selector"])
                rows = [r for r in rows
                        if len(r) >= sel["purchases"].get("row_min_length", 12)]
                print(f"\n  PURCHASES TAB — row_selector matched {len(rows)} row(s)")
                for r in rows[:8]:
                    print(f"    · {r[:100]}")
                if not rows:
                    print("    (nothing — send me the page text below so I can "
                          "fix row_selector)")
            else:
                reading = read_live_tab(text, sel)
                print("\n  pattern            result")
                for name in ("giveaway_present", "entries", "prize",
                             "buyers_only", "followers_only", "challenge"):
                    pat = sel["live"].get(name)
                    hit = first_group(pat, text) if pat else None
                    print(f"    {name:18} {hit if hit else '— no match'}")
                print(f"\n  reading: {reading}")
            print("\n  --- first 1200 chars of visible text ---")
            print("  " + text[:1200].replace("\n", "\n  "))
            print()
    finally:
        browser.close()
        pw.stop()


def cmd_test(cfg: dict) -> None:
    notify.make_notifier(cfg).send(
        "✅ Watcher test ✅", "Tap to open Whatnot.", BASE_URL,
        priority="high", group="test")
    print("Sent — check your phone.")


def cmd_watch(cfg: dict, sel: dict) -> None:
    if not chrome.ensure_running(cfg):
        sys.exit("Could not get a browser up. Nothing was changed.")
    notifier = notify.make_notifier(cfg)
    state = load_json(STATE_PATH, {})
    state.setdefault("seen_purchases", {})
    poll = max(1, int(cfg.get("poll_seconds", 2)))
    confirm = max(1, int(cfg.get("confirm_polls", 2)))
    silence_after = max(0, int(cfg.get("silence_warning_minutes", 45))) * 60
    watch_purchases = bool(cfg.get("watch_purchases", True))

    pw, browser = attach(int(cfg.get("debug_port", 9222)))
    trackers, challenged, warned_tabs = {}, set(), set()
    last_match = time.monotonic()
    warned_silent = False
    log("attached — watching your tabs. Nothing here opens or closes them.")

    try:
        while True:
            pages = whatnot_pages(browser)
            live_urls = set()

            for page in pages:
                try:
                    info = page.evaluate(PAGE_TEXT)
                except Exception:
                    continue                    # tab closed mid-poll
                url, text = info["url"], info["text"]

                if watch_purchases and matches(sel["purchases"].get("url_match"), url):
                    check_purchases(page, sel, state, notifier)
                    continue
                if "/live/" not in url:
                    continue

                live_urls.add(url)
                reading = read_live_tab(text, sel)

                if reading["challenge"]:
                    if url not in challenged:
                        challenged.add(url)
                        log(f"bot challenge in {url} — leaving that tab alone")
                        try:
                            notifier.send(
                                "⚠️ Challenge on a tab ⚠️",
                                "One of your stream tabs is showing a Cloudflare "
                                "check. It is not being watched. Close it or "
                                "solve it yourself.",
                                url, priority="high", group="alerts")
                        except Exception:
                            pass
                    continue
                challenged.discard(url)

                if reading["present"]:
                    last_match = time.monotonic()
                    warned_silent = False

                tracker = trackers.setdefault(url, TabTracker(confirm))
                event = tracker.update(reading, signature(url, reading))
                if event == "started":
                    announce_giveaway(notifier, url, reading)
                elif event == "ended":
                    log(f"giveaway ended — {url}")

            for gone in set(trackers) - live_urls:
                trackers.pop(gone, None)

            if not pages and not warned_silent:
                log("no Whatnot tabs open — waiting")

            # The silent failure of this design is Whatnot changing their
            # markup: everything keeps running and nothing is ever detected.
            if (silence_after and not warned_silent
                    and time.monotonic() - last_match > silence_after
                    and live_urls):
                warned_silent = True
                log("nothing has matched on any tab for a long time — run "
                    "`python watch.py probe`; the selectors may be stale")
                try:
                    notifier.send(
                        "⚠️ Watcher may be blind ⚠️",
                        "No giveaway text has matched on any open tab for a "
                        "while. Whatnot may have changed their page. Run probe.",
                        BASE_URL, priority="default", group="alerts")
                except Exception:
                    pass

            save_state(state)
            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        log("stopping — your tabs are untouched.")
        try:
            save_state(state)
            browser.close()
        except Exception:
            pass
        pw.stop()


def announce_giveaway(notifier, url: str, reading: dict) -> None:
    prize = reading.get("prize") or "giveaway"
    entries = reading.get("entries")
    bits = []
    if entries is not None:
        bits.append(f"{entries} entries")
    if reading.get("followers_only"):
        bits.append("followers only")
    detail = " · ".join(bits)

    if reading.get("buyers_only"):
        # Not enterable without buying in that show, so it is noted, not pushed.
        log(f"buyers-only giveaway (not alerting) — {prize} — {url}")
        return

    log(f"GIVEAWAY — {prize} {('(' + detail + ')') if detail else ''} — {url}")
    try:
        notifier.send(f"🎁 {prize} 🎁", detail or "Tap to enter.", url,
                      priority="max", group="giveaways")
    except Exception as exc:
        log(f"notification failed ({exc.__class__.__name__}) — will retry")


def check_purchases(page, sel: dict, state: dict, notifier) -> None:
    """New rows on the purchases tab are wins or buys."""
    conf = sel["purchases"]
    try:
        rows = page.evaluate(ROWS_JS, conf["row_selector"])
    except Exception:
        return
    minimum = conf.get("row_min_length", 12)
    for row in rows:
        if len(row) < minimum or row in state["seen_purchases"]:
            continue
        state["seen_purchases"][row] = datetime.now(timezone.utc).isoformat()
        # First sight of the tab would otherwise announce your entire history.
        if state.get("purchases_seeded"):
            won = matches(conf.get("win_hint"), row)
            log(f"{'WIN' if won else 'purchase'} — {row[:80]}")
            try:
                notifier.send("🏆 You won! 🏆" if won else "🛒 Purchase 🛒",
                              row[:160], f"{BASE_URL}/?activityTab=purchases",
                              priority="max" if won else "default",
                              group="results")
            except Exception as exc:
                log(f"notification failed ({exc.__class__.__name__})")
    state["purchases_seeded"] = True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="watch",
                    choices=["watch", "probe", "test"])
    args = ap.parse_args()

    cfg = chrome.load_config()          # creates it from the example if absent
    sel = load_json(SELECTORS_PATH)

    if args.command == "probe":
        cmd_probe(cfg, sel)
    elif args.command == "test":
        cmd_test(cfg)
    else:
        cmd_watch(cfg, sel)


if __name__ == "__main__":
    main()
