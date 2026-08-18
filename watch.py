#!/usr/bin/env python3
"""Watch the Whatnot tabs YOU opened, and buzz your phone on a giveaway.

    python watch.py            # start the browser if needed, then watch
    python watch.py probe      # dump what it can see, and fix nothing
    python watch.py react      # can the prize be read WITHOUT opening the banner?
    python watch.py test       # send a test notification

One command. It starts the browser if it isn't already up, attaches, and then
waits — you open your streams whenever you like, before or after, and it picks
them up. Reading the page rather than driving it is the default. It never
navigates, opens, closes or reloads a tab, and never enters a giveaway — that
is Whatnot's rule and this tool does not bend it.

Two narrow exceptions, both added 2026-08-13:

* The purchases tab never refreshes its own data — confirmed by hand, it is a
  one-shot query with no socket and no self-poll, so a real win could sit
  unseen forever. Fixed by calling the page's own already-mounted Apollo
  query's refetch() on a slow interval (`purchases_poll_seconds`) — the same
  request the app makes on a manual reload, just triggered by us instead.
* The giveaway banner's prize, entry rule (followers/buyers/domestic-only),
  YOUR OWN eligibility, and the winner are all absent from the DOM and from
  React state while collapsed — confirmed by probing both, in depth, see
  SPEC.md. But the WebSocket frames that already arrive to drive the visible
  entry counter carry all four (`giveaway_started`, `giveaway_entered`,
  `giveaway_won`), and those are read passively — no request of ours
  involved. An earlier probe here concluded an already-open socket is
  invisible at attach time ("measured, zero frames"); that measurement
  blocked on a plain time.sleep(), which starves this driver's event
  dispatch entirely regardless of what is really arriving. Waiting via
  page.wait_for_timeout() instead surfaced real frames immediately —
  confirmed live against several real giveaways, including two real draws.
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
# What the watcher can see, published for the panel. Without this the panel
# opened its own Playwright connection every 2.5s alongside the watcher's,
# which raced and intermittently reported "the browser is not running".
TABS_PATH = PROJECT_DIR / "tabs.json"
# The raw giveaway_won frame, overwritten on every draw. entrants_from()'s
# read of giveawayEntries is unverified against a real frame — this exists
# so a mis-parse can be diagnosed from what Whatnot actually sent, without
# adding a second, riskier CDP capture path just to look once.
DEBUG_ENTRANTS_PATH = PROJECT_DIR / "debug-entrants.json"
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


def prune_state(state: dict) -> None:
    """Forget giveaways older than the window a win could plausibly relate to,
    so a long run does not grow state.json without bound."""
    cutoff = time.time() - WIN_MEMORY_SECONDS
    recent = state.get("recent_giveaways", {})
    for url in [u for u, i in recent.items() if i.get("at", 0) < cutoff]:
        del recent[url]


def publish_tabs(tabs: list) -> None:
    """Hand the panel a snapshot instead of making it attach for itself."""
    try:
        tmp = TABS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"at": time.time(), "browser": True,
                                   "tabs": tabs}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(TABS_PATH)
    except OSError:
        pass          # a snapshot the panel cannot read is not worth a crash


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


# --- reading a page ---------------------------------------------------------
#
# One evaluate per tab per poll, returning a small struct. All matching is done
# in Python against the page's visible text, so the patterns live in
# selectors.json and a broken selector is a config edit, not a code change.

PAGE_TEXT = r"""() => ({
    url: location.href,
    title: document.title,
    text: (document.body && document.body.innerText || '').slice(0, 20000),
    // innerText is what is RENDERED. The giveaway banner is collapsed by
    // default, so the prize line is not in it. textContent includes nodes the
    // page has built but is not showing, which is often enough to read the
    // title without touching anything. Used only as a fallback for the prize,
    // never for presence or eligibility: those must reflect what is really on
    // screen.
    domText: (document.body && document.body.textContent || '').slice(0, 40000),
    // Cloudflare's own markup, which a real interstitial always carries and
    // an ordinary Whatnot page never does — measured across six live tabs on
    // 2026-08-18: not one of these selectors matched, and none of the pages
    // set window._cf_chl_opt. The wording alone cannot be trusted, because
    // chat is English prose and says things like "just a moment".
    challengeDom: !!(window._cf_chl_opt || document.querySelector(
        'script[src*="challenges.cloudflare.com"], ' +
        'iframe[src*="challenges.cloudflare.com"], ' +
        '#challenge-form, #challenge-running, #cf-chl-widget, ' +
        'form[action*="__cf_chl"]')),
    // The seller, from their own profile link. More reliable than reading it
    // out of the text: the page is full of usernames — every line of chat is
    // one — and the href is unambiguous.
    seller: (() => {
        for (const a of document.querySelectorAll('a[href*="/user/"]')) {
            const m = (a.getAttribute('href') || '').match(/\/user\/([^/?#]+)/);
            if (m) return m[1];
        }
        return '';
    })()
})"""

ROWS_JS = """(sel) => Array.from(document.querySelectorAll(sel))
    .map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim())
    .filter(Boolean)"""


# React keeps a component's props on the DOM node itself, under keys like
# __reactProps$abc123. If Whatnot's giveaway component holds the prize and the
# eligibility flag in its props, they are readable while the banner is still
# folded shut — no click, no event, nothing dispatched. This dumps whatever is
# there so we can find out.
REACT_PROBE = r"""(pattern) => {
    const re = new RegExp(pattern, 'i');
    const seen = new WeakSet();

    function safe(value, depth) {
        if (depth > 5 || value === null || value === undefined) return null;
        const t = typeof value;
        if (t === 'string') return value.length > 300 ? value.slice(0, 300) + '…' : value;
        if (t === 'number' || t === 'boolean') return value;
        if (t !== 'object') return undefined;
        if (value instanceof Node || value instanceof Window) return undefined;
        if (seen.has(value)) return undefined;
        seen.add(value);
        if (Array.isArray(value)) return value.slice(0, 8).map(v => safe(v, depth + 1));
        const out = {};
        for (const k of Object.keys(value).slice(0, 60)) {
            if (k === 'children' || k === '_owner' || k === '_store') continue;
            const v = safe(value[k], depth + 1);
            if (v !== undefined) out[k] = v;
        }
        return out;
    }

    // A function component keeps its state in a linked list of hooks hanging
    // off memoizedState — NOT in memoizedProps. The giveaway panel's props
    // turned out to hold only its own name, so the prize has to be here.
    function hooks(fiber) {
        const out = [];
        let h = fiber && fiber.memoizedState, i = 0;
        while (h && i < 25) {
            if (h.memoizedState !== undefined && h.memoizedState !== null) {
                const v = safe(h.memoizedState, 0);
                if (v !== undefined && v !== null && !(typeof v === 'object' && !Object.keys(v).length))
                    out.push({hook: i, value: v});
            }
            h = h.next; i++;
        }
        return out;
    }

    function label(f) {
        const t = f.type;
        const n = (t && (t.displayName || t.name)) || (typeof t === 'string' ? t : '');
        const cn = f.memoizedProps && f.memoizedProps.componentName;
        return String(cn || n || '?').slice(0, 50);
    }

    let strong = null;
    for (const el of document.querySelectorAll('strong, span, p, div')) {
        if (el.children.length === 0 && re.test(el.textContent || '')) { strong = el; break; }
    }
    if (!strong) return {found: false};

    // Climb to the giveaway component itself, then dump its whole subtree.
    let fiber = null;
    for (const k of Object.keys(strong)) {
        if (k.startsWith('__reactFiber')) { fiber = strong[k]; break; }
    }
    let root = fiber, up = 0, foundNamed = false;
    while (root && up < 20) {
        const cn = root.memoizedProps && root.memoizedProps.componentName;
        if (cn && /giveaway/i.test(String(cn))) { foundNamed = true; break; }
        root = root.return; up++;
    }
    if (!foundNamed) {            // fall back to a few levels up from the text
        root = fiber; let n = 0;
        while (root && root.return && n < 10) { root = root.return; n++; }
    }

    const nodes = [];
    const stack = [root];
    let visited = 0;
    while (stack.length && visited < 4000 && nodes.length < 120) {
        const f = stack.pop();
        if (!f) continue;
        visited++;
        const props = f.memoizedProps && typeof f.memoizedProps === 'object'
            ? safe(f.memoizedProps, 0) : null;
        const state = hooks(f);
        if ((props && Object.keys(props).length) || state.length)
            nodes.push({component: label(f), props: props, state: state});
        if (f.child) stack.push(f.child);
        if (f.sibling) stack.push(f.sibling);
    }

    return {found: true, foundNamed: foundNamed, tag: strong.tagName,
            text: (strong.textContent || '').slice(0, 120), nodes: nodes};
}"""


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


def parse_count(raw) -> int | None:
    """Turn whatever the page wrote into a number.

    Big giveaways are where the count stops being a bare integer: "1,234" in
    English, "1.234" in Dutch, "1 234" with a thin space, "12k" when the UI
    shortens it. Both separators are treated as grouping — the count is
    informational, and being off by a factor is better than the pattern
    failing to match, which used to hide the whole giveaway.
    """
    if raw is None:
        return None
    s = re.sub(r"[\s  ]", "", str(raw))
    thousands = s[-1:].lower() == "k"
    if thousands:
        s = s[:-1]
    if not s:
        return None
    if thousands:
        # "1.5k" is a decimal; "1,5k" is the same thing written elsewhere.
        s = s.replace(",", ".")
        try:
            return int(float(s) * 1000)
        except ValueError:
            return None
    s = s.replace(",", "").replace(".", "")
    return int(s) if s.isdigit() else None


def read_prize(live: dict, text: str, dom_text: str = "") -> str:
    """The prize name, from the visible text if possible, the DOM if not.

    Collapsed, the banner shows only the entry count — so on screen there is no
    title to read. If the page has built the node anyway, textContent has it.
    If it has not, this returns "" and the alert says "giveaway", which is the
    honest outcome rather than a guess.
    """
    return (first_group(live.get("prize"), text)
            or (first_group(live.get("prize"), dom_text) if dom_text else None)
            or "")


# A real Cloudflare interstitial replaces the entire page: a line of text, a
# spinner, nothing else. Six healthy live tabs measured 2026-08-18 carried
# 2781-5856 characters of visible text, so anything near this size is a
# working stream, whatever wording happens to be in its chat.
CHALLENGE_MAX_TEXT = 1500


def read_live_tab(text: str, sel: dict, dom_text: str = "",
                  seller: str = "", challenge_dom: bool = False) -> dict:
    """Turn a stream tab's text into a giveaway reading.

    `text` is what is on screen; `dom_text` may additionally contain nodes the
    page has built but collapsed. Only the prize falls back to the second one.
    """
    live = sel.get("live", {})
    # Wording alone is not enough. Measured 2026-08-18: a healthy tab alerted
    # as challenged because the phrase was somewhere in a full page — chat is
    # English prose and "just a moment" is ordinary English. A real challenge
    # brings Cloudflare's own markup, or at minimum a page with nothing else
    # on it, so require one of those two before believing it.
    if matches(live.get("challenge"), text):
        if challenge_dom or len(text) < CHALLENGE_MAX_TEXT:
            return {"challenge": True, "present": False}
        # Kept so the near-miss is visible in the log rather than silent —
        # if Whatnot ever serves a challenge inside a full-looking page,
        # this is the line that will say so.
        phrase_only = True
    else:
        phrase_only = False
    present = matches(live.get("giveaway_present"), text)
    entries = parse_count(first_group(live.get("entries"), text))
    # Tri-state on purpose. The banner is collapsed by default, and collapsed it
    # shows only the entry count — the prize and the eligibility line are not on
    # screen at all. None means "we cannot see", which is not the same as "you
    # cannot enter", and the two must not be conflated.
    eligible = None
    if present:
        if matches(live.get("not_eligible"), text):
            eligible = False
        elif matches(live.get("enterable"), text):
            eligible = True
    # Shown over the video the moment a giveaway is drawn, naming whoever won.
    # Read on every poll, not only while a giveaway is "present": by the time
    # the overlay appears the banner has usually gone.
    winner = first_group(live.get("winner"), text) or ""
    return {
        "challenge": False,
        "challenge_phrase": phrase_only,
        "present": present,
        "winner": winner,
        # The link is authoritative; the text pattern is only a fallback for
        # when the markup changes and the href is no longer there.
        "seller": seller or first_group(live.get("seller"), text) or "",
        "prize": (read_prize(live, text, dom_text) if present else ""),
        "entries": entries if present else None,
        "eligible": eligible,
    }



def expand_giveaway(page, sel: dict) -> bool:
    """Click the collapsed giveaway banner open. Opt-in, and off by default.

    This is the only thing in the tool that touches a page. Collapsed, the
    prize and the eligibility line are genuinely absent from the DOM — the
    section holds nothing but the header row — so no amount of reading gets
    them; a click is the only route.

    Measured: the click is indistinguishable at the event level. isTrusted is
    true, and with the keep-alive flags even a background tab reports itself
    visible and focused. What is NOT measured, and cannot be from here, is
    whether Whatnot minds the pattern: a click with no mouse movement before
    it, at the instant a giveaway appears, across several tabs at once.

    Kept narrow on purpose — one click, on the chevron inside the section that
    holds the giveaway text, once per giveaway. It never touches anything else,
    and it never touches an entry button.
    """
    selector = (sel.get("live") or {}).get("expand_button")
    if not selector:
        return False
    try:
        button = page.locator(selector).first
        if button.count() == 0:
            return False
        button.click(timeout=2000)
        return True
    except Exception:
        return False          # not there, not clickable, already open


def signature(url: str, reading: dict) -> str:
    """Identity of a giveaway, for deduping.

    The DOM carries no giveaway id, so a chained second giveaway is told from
    the first by its prize. Entry count is deliberately NOT part of this: it
    changes every few seconds and would make every poll a new giveaway.
    """
    return f"{url}|{reading.get('prize', '')}|{reading.get('eligible')}"


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
    or close during a run are picked up without restarting.

    Returns None if the browser has gone away — closing every tab or quitting
    Chrome drops the connection, and that is a reason to stop, not a crash.
    """
    out = []
    try:
        contexts = list(browser.contexts)
    except Exception:
        return None
    for ctx in contexts:
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
                reading = read_live_tab(text, sel, info.get("domText", ""),
                                          info.get("seller", ""),
                                          bool(info.get("challengeDom")))
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


REACT_DUMP_PATH = PROJECT_DIR / "probe-react.json"

INTERESTING = ("giveaway", "prize", "eligib", "buyer", "follow", "title",
               "name", "entr", "qualif", "reward", "product")


def cmd_probe_react(cfg: dict, sel: dict) -> None:
    """Can the prize and eligibility be read WITHOUT opening the banner?

    Whatnot is a React app, and React hangs a component's props off the DOM
    node. If the collapsed banner's component already knows what it is holding,
    those props have the answer and nothing needs clicking.

    Writes everything to probe-react.json — the interesting part is likely to
    be a nested blob, too big to read off a terminal.
    """
    pw, browser = attach(int(cfg.get("debug_port", 9222)))
    pattern = (sel.get("live") or {}).get("giveaway_present", "Giveaway with")
    dumps = []
    try:
        pages = [p for p in whatnot_pages(browser) if "/live/" in p.url]
        if not pages:
            print("\n  No stream tabs open. Open one with a giveaway running.\n")
            return
        for page in pages:
            try:
                found = page.evaluate(REACT_PROBE, pattern)
            except Exception as exc:
                print(f"  ! {page.url}: {exc.__class__.__name__}")
                continue
            dumps.append({"url": page.url, **found})
            print("=" * 72)
            print(f"  {page.url}")
            if not found.get("found"):
                print("  no collapsed giveaway banner on this tab — is one running?")
                continue
            print(f"  anchored on <{found.get('tag')}>: "
                  f"{(found.get('text') or '').strip()[:60]!r}")
            print(f"  found the giveaway component by name: "
                  f"{found.get('foundNamed')}")
            nodes = found.get("nodes", [])
            print(f"  {len(nodes)} component(s) in its subtree")
            hits = []
            for n in nodes:
                blob = json.dumps(n, ensure_ascii=False).lower()
                for word in INTERESTING:
                    if word in blob:
                        where = "state" if n.get("state") else "props"
                        hits.append((n["component"], word, where))
                        break
            if hits:
                print("  giveaway-shaped data:")
                for comp, word, where in hits[:15]:
                    print(f"    {comp[:34]:36} '{word}' in {where}")
            else:
                print("  nothing giveaway-shaped in props or hook state")

    finally:
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()

    REACT_DUMP_PATH.write_text(json.dumps(dumps, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    print("=" * 72)
    print(f"\n  Full dump written to {REACT_DUMP_PATH.name} — send me that file.")
    print("  Run it once with the banner FOLDED (that is the real question),")
    print("  then open the banner and run it again so there is something to")
    print("  compare against.\n")


def cmd_test(cfg: dict) -> None:
    notify.make_notifier(cfg).send(
        "✅ Watcher test ✅", "Tap to open Whatnot.", BASE_URL,
        priority="high", group="test")
    print("Sent — check your phone.")


# --- reading the giveaway itself from the socket -----------------------
#
# Confirmed live 2026-08-13: the frames that already arrive on a live tab to
# drive the visible entry counter also carry everything else about the
# giveaway — prize, entry rule, YOUR OWN eligibility, and eventually the
# winner — none of which ever reaches the DOM or React state while the
# banner is collapsed (see the module docstring). This only reads frames
# already delivered to the tab; it never sends anything of its own.
#
# Frame shape (Socket.IO-ish, undocumented, may drift):
#   [ackId, null, "auction:<livestreamId>" | "commerce:<livestreamId>",
#    eventName, data]
#
# Events used here, by name:
#   giveaway_started            — fires the instant a giveaway begins. data
#                                  is {"id", "product": {...}, "giveaway"}
#                                  where product.id == data.id; the single
#                                  most authoritative "this is the current
#                                  giveaway" signal there is.
#   giveaway_entry_count_updated — {"entryCount", "productId"}; the ongoing
#                                  heartbeat that also names the current
#                                  product for tabs where _started was missed
#                                  (already open before we attached).
#   product_updated / product_pinned / auction_started — same product-record
#                                  shape as giveaway_started's "product", but
#                                  fires for every product, giveaway or not.
#                                  Every one carries a "giveaway" sub-object,
#                                  mostly false/null; only a real giveaway
#                                  sets any flag true, and onlyDomestic in
#                                  particular defaults true on ordinary
#                                  listings — so this alone is not proof,
#                                  only the entry-count/started events say
#                                  which product id is on screen right now.
#   giveaway_won                 — fires once, at the draw. data.giveaway
#                                  .productId names which giveaway; the
#                                  winner is data.product.purchaserUser
#                                  .username — winning is modelled as buying
#                                  the item for €0, so the winner is simply
#                                  whoever "purchased" it. data.giveawayEntries
#                                  is every entrant, not the winner — do not
#                                  confuse the two.
#   giveaway_entered              — fires for OUR OWN account's entry state:
#                                  {"giveaway": {"productId"},
#                                   "isUserEligibleForGiveaway": bool}. This
#                                  is the one thing expand_giveaway used to
#                                  exist for — personal eligibility — now
#                                  read passively instead of by clicking.

_GIVEAWAY_TYPE_EVENTS = ("product_updated", "product_pinned", "auction_started",
                        "giveaway_started")


def entrants_from(raw) -> set[str] | None:
    """Lowercase usernames out of a giveaway_won frame's entrant list.

    The shape of each entry is not documented anywhere we can read (unlike
    the winner, which is modelled as a purchase and so has a known
    purchaserUser field) — try every plausible spot a username could sit
    and skip whatever does not match. Returns None (not an empty set) when
    raw is not a list at all, so callers can tell "no entrants field" apart
    from "an empty giveaway".
    """
    if not isinstance(raw, list):
        return None
    out = set()
    for item in raw:
        if isinstance(item, str):
            out.add(item.lower())
            continue
        if not isinstance(item, dict):
            continue
        for spot in (item, item.get("user"), item.get("purchaserUser")):
            if isinstance(spot, dict) and isinstance(spot.get("username"), str):
                out.add(spot["username"].lower())
                break
    return out


def attach_giveaway_socket(page, ws_state: dict) -> None:
    """Start passively learning this tab's giveaway content, once per tab.

    Best-effort: a tab that will not attach (closed mid-poll, CDP hiccup) is
    silently skipped and retried on a later poll — this must never be the
    reason a giveaway alert is delayed or dropped.
    """
    url = page.url
    if url in ws_state:
        return
    try:
        cdp = page.context.new_cdp_session(page)
        cdp.send("Network.enable")
    except Exception:
        return

    # winner: set once by on_frame (callback thread), consumed exactly once
    # by pop_ws_winner (main thread) — a plain dict key survives that under
    # the GIL without a lock, same as active_id/products already do below.
    entry = {"products": {}, "active_id": None, "winner": None,
             "counts": {}, "cdp": cdp}

    def on_frame(params):
        payload = params.get("response", {}).get("payloadData", "")
        try:
            arr = json.loads(payload)
        except (ValueError, TypeError):
            return
        if not isinstance(arr, list) or len(arr) < 5:
            return
        event, data = arr[3], arr[4]
        if not isinstance(data, dict):
            return
        if event == "giveaway_entry_count_updated":
            pid = data.get("productId")
            if pid:
                entry["active_id"] = pid
                # The real pool size at the draw — needed to tell a complete
                # entrant list (<=50) from a truncated one, which is the only
                # case absence from the list means anything (see the shadow
                # tally below). Keep the latest tick per product.
                count = data.get("entryCount")
                if isinstance(count, int):
                    entry["counts"][pid] = count
        elif event in _GIVEAWAY_TYPE_EVENTS:
            product = data.get("product") if "product" in data else data
            if not isinstance(product, dict):
                return
            pid, giveaway = product.get("id"), product.get("giveaway")
            if pid and giveaway:
                entry["products"][pid] = {
                    "name": product.get("name") or "",
                    "buyerAppreciation": bool(giveaway.get("buyerAppreciation")),
                    "onlyFollowers": bool(giveaway.get("onlyFollowers")),
                    "onlyDomestic": bool(giveaway.get("onlyDomestic")),
                    "onlyTriviaWinners": bool(giveaway.get("onlyTriviaWinners")),
                    "partyPurchase": bool(giveaway.get("partyPurchase")),
                    "eligible": entry["products"].get(pid, {}).get("eligible"),
                }
            # giveaway_started is the clearest possible "this one is current"
            # signal — no reason to wait for the next entry-count tick.
            if event == "giveaway_started" and pid:
                entry["active_id"] = pid
        elif event == "giveaway_won":
            gv = data.get("giveaway") or {}
            product = data.get("product") or {}
            purchaser = product.get("purchaserUser") or {}
            pid, username = gv.get("productId"), purchaser.get("username")
            if pid and username:
                entry["winner"] = {
                    "pid": pid, "username": username.lower(),
                    "prize": product.get("name") or "",
                    # Every entrant, not just the winner — shape isn't
                    # documented anywhere, so pull every plausible username
                    # spot rather than assume one. Worst case: an entry we
                    # can't read is silently skipped, never a crash.
                    "entrants": entrants_from(data.get("giveawayEntries")),
                    "was_eligible": entry["products"].get(pid, {}).get("eligible"),
                    "entry_count": entry["counts"].get(pid),
                }
                try:
                    DEBUG_ENTRANTS_PATH.write_text(json.dumps(
                        {"at": time.time(), "url": url, "pid": pid,
                         "winner": username, "raw_entries": data.get("giveawayEntries")},
                        indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
        elif event == "giveaway_entered":
            gv = data.get("giveaway") or {}
            pid = gv.get("productId")
            eligible = data.get("isUserEligibleForGiveaway")
            if pid and eligible is not None:
                entry["products"].setdefault(pid, {})["eligible"] = bool(eligible)

    try:
        cdp.on("Network.webSocketFrameReceived", on_frame)
    except Exception:
        return
    ws_state[url] = entry


def current_giveaway_info(ws_state: dict, url: str) -> dict | None:
    """The learned prize + entry-rule for whichever product the socket most
    recently named as the active giveaway on this tab, or None until both a
    count update (or giveaway_started) and that product's own record have
    arrived."""
    entry = ws_state.get(url)
    if not entry or not entry["active_id"]:
        return None
    return entry["products"].get(entry["active_id"])


def pop_ws_winner(ws_state: dict, url: str) -> dict | None:
    """The most recent giveaway_won on this tab, consumed once so the same
    draw is never handed to the caller twice."""
    entry = ws_state.get(url)
    if not entry:
        return None
    winner, entry["winner"] = entry["winner"], None
    return winner


# --- where the seller ships from -------------------------------------------
#
# "Domestic only" restricts a giveaway to the seller's own country, so that
# country is what turns onlyDomestic into "can I actually enter this". The
# socket does not carry it — measured 2026-08-18, a product_updated frame has
# the price currency, the seller's user id and the listing id, and nothing
# geographic. The page's own Apollo cache does:
#
#   ListingNode.shippingDetails  "Ships from United Kingdom" / "Ships from
#                                Germany", and null for a seller in YOUR own
#                                country — Whatnot renders that line only when
#                                the two countries differ. Measured across five
#                                live streams: 2 NL sellers, 34 listings, all
#                                null; 3 foreign sellers, 53 listings, every
#                                one naming its country.
#   AddressNode.countryCode      your own country, so nothing here is pinned
#                                to one buyer.
#   getShippingQuotes(…).quotes[].shippingEstimate.tariffDisclosures
#                                "Since this seller ships from GB, NL Customs
#                                may charge tariffs…" — both countries as ISO
#                                codes, which beats matching country names,
#                                but empty inside the EU (a German seller gets
#                                none), so it can confirm and never carry this
#                                on its own.
#
# Currency is not a substitute: a German seller prices in EUR exactly like a
# Dutch one, so it separates GB and US sellers only.

SELLER_ORIGIN_JS = r"""
    () => {
        const client = window.__APOLLO_CLIENT__;
        if (!client) return {error: "no apollo client on this page"};
        const data = client.cache.extract();
        let listings = 0, shipsFrom = "", buyerCountry = "";
        for (const value of Object.values(data)) {
            if (!value || typeof value !== "object") continue;
            if (value.__typename === "ListingNode") {
                listings++;
                if (typeof value.shippingDetails === "string" &&
                    value.shippingDetails) shipsFrom = value.shippingDetails;
            } else if (value.__typename === "AddressNode" && value.countryCode) {
                buyerCountry = value.countryCode;
            }
        }
        // The one place both countries appear as ISO codes. Only written when
        // customs actually apply, so it is absent for every EU seller.
        let pair = null;
        for (const [key, value] of Object.entries(data.ROOT_QUERY || {})) {
            if (!key.startsWith("getShippingQuotes") || !value) continue;
            for (const quote of value.quotes || []) {
                const est = quote && quote.shippingEstimate;
                for (const line of (est && est.tariffDisclosures) || []) {
                    const m = String(line).match(
                        /ships from ([A-Z]{2}),\s*([A-Z]{2})\s+Customs/);
                    if (m) pair = {seller: m[1], buyer: m[2]};
                }
            }
        }
        return {ok: true, listings, shipsFrom, buyerCountry, pair};
    }
"""

# Only needed to tell YOUR country code apart from the country NAME Whatnot
# writes into shippingDetails. A code that is missing here reads as unknown,
# never as foreign — see read_seller_origin().
COUNTRY_NAMES = {
    "NL": "netherlands", "BE": "belgium", "DE": "germany", "FR": "france",
    "GB": "united kingdom", "UK": "united kingdom", "IE": "ireland",
    "ES": "spain", "PT": "portugal", "IT": "italy", "AT": "austria",
    "CH": "switzerland", "LU": "luxembourg", "PL": "poland", "CZ": "czechia",
    "DK": "denmark", "SE": "sweden", "NO": "norway", "FI": "finland",
    "US": "united states", "CA": "canada", "AU": "australia",
    "NZ": "new zealand", "JP": "japan",
}


def origin_from_cache(result: dict, previous: dict | None = None) -> dict:
    """Turn one SELLER_ORIGIN_JS reading into {ships_from, buyer_country,
    foreign}, where `foreign` stays None until it is genuinely known.

    Deliberately asymmetric. Every uncertainty — no listings cached yet, a
    country name we cannot match against your own code — leaves `foreign`
    None, and None means "announce it anyway". Being unsure costs a spare
    buzz; being wrong the other way costs a giveaway.
    """
    origin = {
        "ships_from": str(result.get("shipsFrom") or ""),
        "buyer_country": str(result.get("buyerCountry") or ""),
        "listings": int(result.get("listings") or 0),
        "foreign": None,
    }
    if previous:
        # The buyer address is not always in a fresh tab's cache; the seller's
        # country is. Neither can change mid-stream, so never lose one.
        origin["ships_from"] = origin["ships_from"] or previous.get("ships_from", "")
        origin["buyer_country"] = (origin["buyer_country"]
                                   or previous.get("buyer_country", ""))
    pair = result.get("pair")
    if isinstance(pair, dict) and pair.get("seller") and pair.get("buyer"):
        origin["foreign"] = pair["seller"] != pair["buyer"]
        origin["ships_from"] = origin["ships_from"] or f"ships from {pair['seller']}"
    elif origin["ships_from"]:
        home = COUNTRY_NAMES.get(origin["buyer_country"].upper())
        if home:
            origin["foreign"] = home not in origin["ships_from"].lower()
    elif origin["listings"]:
        # Listings are cached for this stream and not one of them carries a
        # "Ships from" line, which Whatnot omits only when the seller is in
        # your own country.
        origin["foreign"] = False
    return origin


def read_seller_origin(page, origin_state: dict, url: str) -> dict:
    """Where this stream's seller ships from, learned once per tab.

    Kept as soon as it is known: a seller cannot move country mid-show, and
    re-reading the cache every poll would spend an evaluate per tab on an
    answer that cannot change. Any failure returns whatever was already
    known (possibly nothing), never raises — this is a filter on alerts, and
    it must never be the reason one does not arrive.
    """
    previous = origin_state.get(url)
    if previous and previous.get("foreign") is not None:
        return previous
    try:
        result = page.evaluate(SELLER_ORIGIN_JS)
    except Exception:
        return previous or {}
    if not isinstance(result, dict) or not result.get("ok"):
        return previous or {}
    origin = origin_from_cache(result, previous)
    if origin["foreign"] is not None and (not previous
                                          or previous.get("foreign") is None):
        where = origin["ships_from"] or "your own country"
        log(f"seller origin — {where}"
            f"{' (foreign)' if origin['foreign'] else ' (domestic)'} — {url}")
    origin_state[url] = origin
    return origin


# A complete entrant list. The predecessor project measured that
# giveawayEntries truncates at 50 names regardless of the real pool, so only
# at or below this can absence from the list mean you were left out rather
# than merely not among the first 50 shown.
COMPLETE_POOL_MAX = 50


def record_draw_for_shadowban(state: dict, reading: dict, ws_winner: dict,
                              my_username: str) -> str | None:
    """Log whether an eligible, enterable, small-enough draw included you.

    Read-only bookkeeping: it never buzzes, it only counts. A draw is tallied
    only when all of these hold, so the ratio it builds is honest:

      * you were eligible (Whatnot's own verdict was True) — otherwise not
        being drawn is expected, not evidence of anything;
      * you could actually enter it — a domestic-only giveaway from a seller
        abroad is one you are barred from, so it is EXCLUDED, never counted
        as a miss (foreign_domestic() non-empty);
      * the entrant list was complete (entryCount <= 50) — above that the
        list is truncated and your absence proves nothing;
      * the list parsed at all (not None).

    Returns a short reason string when it skips, for the log; None when it
    actually recorded one. The counts live in state so they survive restarts.
    """
    if not my_username or not ws_winner:
        return None
    if not ws_winner.get("was_eligible"):
        return None
    if foreign_domestic(reading):
        # Barred from entering — excluded by design, exactly as asked.
        return "foreign domestic-only, not counted"
    count = ws_winner.get("entry_count")
    if not isinstance(count, int) or count > COMPLETE_POOL_MAX:
        return "pool too big to trust the list"
    entrants = ws_winner.get("entrants")
    if entrants is None:
        return "no entrant list on the frame"

    was_in = my_username in entrants
    stats = state.setdefault("shadowban_stats", {"eligible_draws": 0,
                                                 "listed": 0, "missing": 0})
    stats["eligible_draws"] += 1
    stats["listed" if was_in else "missing"] += 1
    prize = ws_winner.get("prize") or "giveaway"
    if was_in:
        log(f"draw tally: IN the pool ({count} entrants) — {prize}")
    else:
        log(f"draw tally: NOT in a complete pool of {count} — {prize} "
            f"[eligible {stats['eligible_draws']}, missing {stats['missing']}]")
    return None


def foreign_domestic(reading: dict) -> str:
    """"Ships from Germany"-ish, when this giveaway is domestic-only AND the
    seller is abroad — i.e. the case you cannot enter. Empty string whenever
    either half is unknown or does not apply."""
    ws_giveaway = reading.get("ws_giveaway") or {}
    origin = reading.get("origin") or {}
    if not ws_giveaway.get("onlyDomestic") or not origin.get("foreign"):
        return ""
    return origin.get("ships_from") or "a foreign seller"


def cmd_watch(cfg: dict, sel: dict) -> None:
    if not chrome.ensure_running(cfg):
        sys.exit("Could not get a browser up. Nothing was changed.")
    notifier = notify.make_notifier(cfg)
    state = load_json(STATE_PATH, {})
    state.setdefault("seen_purchases", {})
    # url -> {prize, at}. Lets a win be traced back to its stream.
    state.setdefault("recent_giveaways", {})
    # {eligible_draws, listed, missing} — the running shadowban ratio, over
    # draws you were eligible for, could enter, and whose pool was small
    # enough to trust. See record_draw_for_shadowban().
    state.setdefault("shadowban_stats",
                     {"eligible_draws": 0, "listed": 0, "missing": 0})
    poll = max(1, int(cfg.get("poll_seconds", 2)))
    confirm = max(1, int(cfg.get("confirm_polls", 2)))
    silence_after = max(0, int(cfg.get("silence_warning_minutes", 45))) * 60
    watch_purchases = bool(cfg.get("watch_purchases", True))
    # "shadow" (default), "mute" or "off" — see announce_giveaway().
    foreign_domestic_mode = str(
        cfg.get("foreign_domestic_giveaways", "shadow")).strip().lower()
    purchases_poll_seconds = max(15, int(cfg.get("purchases_poll_seconds", 60)))

    pw, browser = attach(int(cfg.get("debug_port", 9222)))
    trackers, challenged, warned_tabs = {}, set(), set()
    challenge_wording = set()  # tabs where the wording appeared but the page was fine
    expanded = set()          # tabs whose banner we have already asked to open
    won_here = set()          # tabs whose draw overlay we have already alerted on
    ws_state = {}             # url -> learned giveaway info from socket frames
    origin_state = {}         # url -> where that stream's seller ships from
    purchases_last_call = {"t": 0.0}   # throttle for the purchases refetch()
    my_username = str(cfg.get("my_username", "")).strip().lstrip("@").lower()
    if not my_username:
        log("my_username is not set — wins will only be noticed from the "
            "purchases tab, not the moment they are drawn")
    may_expand = bool(cfg.get("expand_giveaway", False))
    if may_expand:
        log("expand_giveaway is ON — this will click the banner open, which is "
            "the one thing here that touches a page")
    last_match = time.monotonic()
    warned_silent = False
    warned_no_tabs = False
    log("attached — watching your tabs. Nothing here opens or closes them.")

    try:
        while True:
            pages = whatnot_pages(browser)
            if pages is None:
                log("the browser was closed — stopping.")
                break
            live_urls = set()
            snapshot = []

            for page in pages:
                try:
                    info = page.evaluate(PAGE_TEXT)
                except Exception:
                    continue                    # tab closed mid-poll
                url, text = info["url"], info["text"]

                if watch_purchases and matches(sel["purchases"].get("url_match"), url):
                    snapshot.append({"url": url, "title": info["title"],
                                     "kind": "purchases"})
                    if time.monotonic() - purchases_last_call["t"] >= purchases_poll_seconds:
                        purchases_last_call["t"] = time.monotonic()
                        check_purchases_api(page, state, notifier)
                    continue
                if "/live/" not in url:
                    snapshot.append({"url": url, "title": info["title"],
                                     "kind": "other"})
                    continue

                live_urls.add(url)
                attach_giveaway_socket(page, ws_state)
                reading = read_live_tab(text, sel, info.get("domText", ""),
                                          info.get("seller", ""),
                                          bool(info.get("challengeDom")))
                reading["origin"] = read_seller_origin(page, origin_state, url)
                ws_info = current_giveaway_info(ws_state, url)
                if ws_info:
                    reading["ws_giveaway"] = ws_info
                    if not reading.get("prize"):
                        reading["prize"] = ws_info.get("name") or ""
                    # Personal eligibility, straight from OUR account's own
                    # giveaway_entered frame — the one thing that otherwise
                    # required expand_giveaway (a click) to ever see.
                    if ws_info.get("eligible") is not None:
                        reading["eligible"] = ws_info["eligible"]

                snapshot.append({"url": url, "title": info["title"],
                                 "kind": "live", "reading": reading})

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
                if reading.get("challenge_phrase") and url not in challenge_wording:
                    challenge_wording.add(url)
                    log(f"challenge wording on a healthy page (chat, most "
                        f"likely) — not alerting — {url}")

                if reading["present"]:
                    last_match = time.monotonic()
                    warned_silent = False

                # The socket's giveaway_won fires the instant the draw
                # happens — structured, reliable, and (so far) faster than
                # the DOM overlay ever gets confirmed by a poll. The overlay
                # text stays as a backstop for tabs where the socket was
                # already open before we attached and giveaway_won was
                # missed. Both share `won_here`, so whichever fires first
                # is the only one that announces.
                ws_winner = pop_ws_winner(ws_state, url)
                overlay_winner = reading.get("winner", "").lower()
                won_username = (ws_winner["username"] if ws_winner
                                else overlay_winner)
                if my_username and won_username == my_username and url not in won_here:
                    won_here.add(url)
                    announce_win_from_stream(notifier, url,
                                             reading.get("seller", ""), state)
                if not reading["present"] and not reading.get("winner"):
                    won_here.discard(url)

                # Confirmation-only, on purpose — the predecessor project
                # (whatnot-monitor) measured that the giveawayEntries NAME
                # list truncates at 50 regardless of the real pool size
                # (entryCount=105 seen alongside a 50-name list), so seeing
                # your name proves you entered but NOT seeing it proves
                # nothing. It never alarmed on absence for exactly that
                # reason — only ever logged the positive case. Same here:
                # log-only (never notifier.send, this is not for Bark),
                # silent unless we actually saw the name.
                if (ws_winner and ws_winner.get("was_eligible")
                        and won_username != my_username
                        and ws_winner.get("entrants") is not None
                        and my_username in ws_winner["entrants"]):
                    log(f"confirmed in the draw ({len(ws_winner['entrants'])} "
                        f"entries) — {ws_winner.get('prize') or 'giveaway'} — {url}")

                # Same drawn giveaway, counted for the shadowban ratio. Runs
                # only when you won nothing yourself (a win is not a miss),
                # and skips anything you were barred from — see the function.
                if won_username != my_username:
                    record_draw_for_shadowban(state, reading, ws_winner,
                                              my_username)

                # Opt-in, once per giveaway: if it is folded shut there is
                # nothing to read, so ask for it to be opened and pick the
                # detail up on the next poll.
                if (may_expand and reading["present"] and not reading["prize"]
                        and reading["eligible"] is None
                        and url not in expanded):
                    expanded.add(url)
                    if expand_giveaway(page, sel):
                        log(f"opened the giveaway panel on {url}")
                        continue          # read it properly next tick
                if not reading["present"]:
                    expanded.discard(url)

                tracker = trackers.setdefault(url, TabTracker(confirm))
                event = tracker.update(reading, signature(url, reading))
                if event == "started":
                    announce_giveaway(notifier, url, reading,
                                      state["recent_giveaways"],
                                      foreign_domestic_mode)
                elif event == "ended":
                    log(f"giveaway ended — {url}")

            for gone in set(trackers) - live_urls:
                trackers.pop(gone, None)
            for gone in set(origin_state) - live_urls:
                origin_state.pop(gone, None)
            for gone in set(ws_state) - live_urls:
                ws_state.pop(gone, None)

            if not pages and not warned_no_tabs:
                warned_no_tabs = True
                log("no Whatnot tabs open — waiting")
            elif pages:
                warned_no_tabs = False

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

            publish_tabs(snapshot)
            prune_state(state)
            save_state(state)
            # Not time.sleep(): that blocks this process's OS thread, which
            # starves Playwright's own event dispatch along with it — no
            # queued socket-frame callback fires until sleep returns. Waiting
            # through Playwright instead keeps ws_state filling in live,
            # between polls, not just at the moment one happens to land.
            if pages:
                try:
                    pages[0].wait_for_timeout(poll * 1000)
                except Exception:
                    time.sleep(poll)
            else:
                time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        log("stopping — your tabs are untouched.")
        try:
            save_state(state)
        except Exception as exc:
            log(f"could not save state ({exc.__class__.__name__})")
        for shut in (browser.close, pw.stop):
            try:
                shut()
            except Exception:
                pass      # already gone; nothing here is worth a traceback


def describe_giveaway_type(ws_giveaway: dict | None) -> str:
    """A short label for who can enter, learned from the socket rather than
    guessed — 'Followers only', 'Buyers only' — empty if the giveaway is open
    to anyone or we have not learned it yet."""
    if not ws_giveaway:
        return ""
    parts = []
    if ws_giveaway.get("buyerAppreciation"):
        parts.append("Buyers only")
    if ws_giveaway.get("onlyFollowers"):
        parts.append("Followers only")
    if ws_giveaway.get("onlyTriviaWinners"):
        parts.append("Trivia winners only")
    if ws_giveaway.get("partyPurchase"):
        parts.append("Party purchase")
    if ws_giveaway.get("onlyDomestic"):
        parts.append("Domestic only")
    return " · ".join(parts)


def announce_giveaway(notifier, url: str, reading: dict,
                      remember: dict | None = None,
                      foreign_domestic_mode: str = "shadow") -> None:
    """Push in the shape the old radar used, which reads well on a lock screen:
    who it is on the title line, what it is underneath, then what to do.

    No entry count. It is stale the moment it is sent and it pushed the thing
    you actually want — the prize — further down.
    """
    seller = reading.get("seller") or ""
    prize = reading.get("prize") or ""
    eligible = reading.get("eligible")
    type_label = describe_giveaway_type(reading.get("ws_giveaway"))
    abroad = foreign_domestic(reading)

    # Silence only what the page has explicitly said you cannot enter.
    # Unknown is announced: a missed giveaway costs an entry, a spare buzz
    # costs nothing.
    if eligible is False:
        log(f"not eligible, staying quiet — {prize or 'giveaway'} — {url}")
        return

    # Domestic-only from a seller abroad: Whatnot restricts entry to the
    # seller's own country, so this one cannot be entered at all. Unlike
    # `eligible`, which is Whatnot's own verdict on your account, this is
    # inferred from two readings — so it starts in "shadow": it says what it
    # WOULD have silenced and then sends anyway, and only becomes real once
    # the log shows it was right. "off" turns it off entirely.
    if abroad:
        if foreign_domestic_mode == "mute":
            log(f"domestic-only from abroad ({abroad}), staying quiet — "
                f"{prize or 'giveaway'} — {url}")
            return
        if foreign_domestic_mode != "off":
            log(f"WOULD HAVE STAYED QUIET — domestic-only, {abroad} — "
                f"{prize or 'giveaway'} — {url}")
            # Put it in the push too, so the call can be judged from the
            # phone rather than only from the log.
            type_label = f"{type_label} · {abroad}" if type_label else abroad

    title = f"🎁 Giveaway — {seller} 🎁" if seller else "🎁 Giveaway 🎁"
    body = prize or "A giveaway just started."
    if type_label:
        body += f"\n{type_label}"
    body += "\nOpen the app to enter."

    if remember is not None and prize:
        # Kept so a win can be traced back to the stream it came from — the
        # purchases row has no link of its own.
        remember[url] = {"prize": prize, "seller": seller, "at": time.time()}

    log(f"GIVEAWAY — {seller or '?'} — {prize or '(title not on screen)'}"
        f"{' — ' + type_label if type_label else ''} — {url}")
    try:
        notifier.send(title, body, url, priority="max", group="giveaways")
    except Exception as exc:
        log(f"notification failed ({exc.__class__.__name__}) — will retry")


# --- linking a win back to the stream it came from --------------------------
#
# The purchases row carries no link: just a status, a title, a price and a
# date. But we announced that giveaway ourselves minutes earlier and knew the
# stream URL then, so remember it and match the win back by title.
#
# Conservative on purpose. Sending you to the WRONG seller's stream is worse
# than sending you to your purchases page, so a weak match is not used at all.

WIN_MEMORY_SECONDS = 4 * 3600


def normalise_title(title: str) -> list:
    """Comparable tokens: lowercase words and #numbers, plurals folded."""
    cleaned = re.sub(r"[^\w#]+", " ", (title or "").lower())
    out = []
    for tok in cleaned.split():
        if len(tok) < 2 and not tok.startswith("#"):
            continue
        out.append(tok[:-1] if len(tok) > 3 and tok.endswith("s") else tok)
    return out


def title_similarity(a: str, b: str) -> float:
    ta, tb = set(normalise_title(a)), set(normalise_title(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def match_stream(title: str, remembered: dict, now: float,
                 threshold: float = 0.6):
    """The stream URL a win most likely came from, or None.

    None is a perfectly good answer — the notification then points at your
    purchases page, which is always correct if less convenient.
    """
    best, best_score = None, 0.0
    for url, info in remembered.items():
        if now - info.get("at", 0) > WIN_MEMORY_SECONDS:
            continue
        score = title_similarity(title, info.get("prize", ""))
        if score > best_score:
            best, best_score = url, score
    return best if best_score >= threshold else None


def parse_purchases(text: str, sel: dict) -> list:
    """Rows from the activity panel: (status, title, price, date, won).

    Superseded 2026-08-13 by fetch_purchases_via_api() below — this page
    turned out never to refresh its own text on its own, so this regex could
    scrape only whatever was on screen at page-load, forever. Kept, not
    deleted: still what `probe` reports against, and the fallback if a future
    Whatnot build ever removes window.__APOLLO_CLIENT__.

    Read from the text rather than a CSS selector — the shape is stable and
    class names are not. A giveaway win costs nothing, so the price is what
    marks it, which is far more reliable than looking for the word "giveaway":
    plenty of paid listings say that too.
    """
    pattern = sel.get("purchases", {}).get("row")
    if not pattern:
        return []
    try:
        found = re.findall(pattern, text, re.I | re.M)
    except re.error:
        return []
    rows = []
    for status, title, price, date in found:
        try:
            # Anchored on the badge, so a price written into a TITLE cannot be
            # read as the row's price — one real row is called
            # "FREE PACKS & SHIPPING TWV €4,06 #16".
            amount = float(price.replace(" ", "").replace(",", "."))
        except ValueError:
            amount = -1.0
        rows.append({"status": status.strip(), "title": title.strip(),
                     "price": amount, "date": date.strip(),
                     "won": amount == 0.0})
    return rows


GET_MY_PURCHASES_JS = """
    async () => {
        const client = window.__APOLLO_CLIENT__;
        if (!client) return {error: "no apollo client on this page"};
        let target = null;
        for (const [, q] of client.getObservableQueries()) {
            const name = q.queryName || (q.options && q.options.query &&
                q.options.query.definitions[0].name.value);
            if (name === "GetMyPurchases") { target = q; break; }
        }
        if (!target) return {error: "GetMyPurchases is not mounted on this tab"};
        const res = await target.refetch();
        const edges = (res.data && res.data.myOrders &&
            res.data.myOrders.edges) || [];
        return {ok: true, edges: edges.map(e => ({
            uuid: e.node.uuid,
            status: e.node.status,
            prettyStatus: e.node.prettyStatus,
            amount: (e.node.total && e.node.total.amount) ?? -1,
            currency: (e.node.total && e.node.total.currency) || "",
            createdAt: e.node.createdAt,
            title: (e.node.items && e.node.items.edges[0] &&
                e.node.items.edges[0].node.listing &&
                e.node.items.edges[0].node.listing.title) || "",
        }))};
    }
"""


def fetch_purchases_via_api(page) -> list | None:
    """Ask the page's own already-mounted Apollo query to refetch itself —
    the same GetMyPurchases request the app makes on a manual reload, just
    triggered by us. Structured JSON beats the old text regex outright: a
    stable `uuid` instead of a title|date|price composite, and it works at
    all — the purchases page is a one-shot fetch with no socket and no
    self-poll, confirmed by hand 2026-08-13, so the DOM text here never
    changes on its own no matter how long or how often it is read.

    None on any failure — a missing Apollo client, an unmounted query, a
    network hiccup. The caller just tries again next interval; nothing here
    is worth crashing the watcher over.
    """
    try:
        result = page.evaluate(GET_MY_PURCHASES_JS)
    except Exception:
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    return result.get("edges") or []


def announce_win_from_stream(notifier, url: str, seller: str, state: dict) -> None:
    """You won, seen live in the stream rather than found later.

    The overlay is the fastest possible signal — it appears at the draw — and
    it comes with the seller and the stream link already known. The purchases
    tab is still the reliable backstop, because this overlay is on screen for
    only a few seconds and a poll can miss it.
    """
    log(f"WON — {seller or url} (seen in the stream)")
    state.setdefault("stream_wins", {})[url] = time.time()
    try:
        notifier.send("🏆 You won" + (f" — {seller}" if seller else "") + "! 🏆",
                      "Seen live in the stream. Check the app.",
                      url, priority="max", group="results")
    except Exception as exc:
        log(f"win notification failed ({exc.__class__.__name__})")


def check_purchases_api(page, state: dict, notifier) -> None:
    """New orders since the last refetch are wins or buys.

    Keyed by `uuid` — a distinct namespace ("api:...") from the old
    title|date|price keys `seen_purchases` used to hold, so nothing here can
    collide with entries the previous, DOM-scraping version left behind.
    Those old keys just age out of relevance; they are never read by this
    path. First call after upgrading seeds silently, exactly like the old
    version did on its very first run — otherwise every order you have ever
    won or bought would fire a notification at once.
    """
    edges = fetch_purchases_via_api(page)
    if edges is None:
        return
    for row in edges:
        key = f"api:{row['uuid']}"
        if not row["uuid"] or key in state["seen_purchases"]:
            continue
        state["seen_purchases"][key] = datetime.now(timezone.utc).isoformat()
        won = row["amount"] == 0
        # First sight would otherwise announce your entire purchase history.
        if not state.get("purchases_api_seeded"):
            continue
        # Point a win at the stream it came from, so the notification behaves
        # like the giveaway one did. Falls back to the purchases page whenever
        # the match is not clear-cut: the wrong seller's stream would be worse
        # than a generic link.
        link = f"{BASE_URL}/?activityTab=purchases"
        seller, where = "", ""
        if won:
            match = match_stream(row["title"], state.get("recent_giveaways", {}),
                                 time.time())
            if match:
                link = match
                seller = (state["recent_giveaways"][match].get("seller") or "")
                where = " — tap to open the stream"
                # Already announced from the draw overlay minutes ago; the row
                # only adds the item name, which is not worth a second buzz.
                seen_live = (state.get("stream_wins", {}) or {}).get(match, 0)
                if time.time() - seen_live < 30 * 60:
                    log(f"WIN (already alerted live) — {row['title'][:60]}")
                    continue
        price = row["amount"] / 100.0 if row["amount"] >= 0 else 0.0
        log(f"{'WIN' if won else 'purchase'} — {row['title'][:70]} "
            f"({price:.2f} {row['currency']}, {row['prettyStatus']}){where}")
        try:
            title = ("🏆 You won" + (f" — {seller}" if seller else "") + "! 🏆"
                     if won else "🛒 Purchase 🛒")
            notifier.send(title, row["title"][:160], link,
                          priority="max" if won else "default",
                          group="results")
        except Exception as exc:
            log(f"notification failed ({exc.__class__.__name__})")
    state["purchases_api_seeded"] = True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="watch",
                    choices=["watch", "probe", "react", "test"])
    args = ap.parse_args()

    cfg = chrome.load_config()          # creates it from the example if absent
    sel = load_json(SELECTORS_PATH)

    if args.command == "probe":
        cmd_probe(cfg, sel)
    elif args.command == "react":
        cmd_probe_react(cfg, sel)
    elif args.command == "test":
        cmd_test(cfg)
    else:
        cmd_watch(cfg, sel)


if __name__ == "__main__":
    main()
