# Whatnot Giveaway Watcher — attach mode

**Spec, not code.** Written 2026-08-12, from findings measured during the
wn-monitor-web debugging session.

## What it is

A giveaway notifier that **never drives the browser**. You open the streams you
want; it attaches to your Chrome and reads what is already on screen. No
navigation, no tab opening or closing, no discovery.

The point is not tidiness. It is that the current radar announces itself:

| | current radar | attach mode |
|---|---|---|
| `navigator.webdriver` | **true** | **false** (measured) |
| automated page loads | ~6 per 5 min | **zero** |
| requests to Whatnot | discovery + every tab load | **zero** |
| unattended | yes | no — you open the tabs |

What you give up is running it overnight. Every show is a new
`whatnot.com/live/<uuid>`, so when a stream ends its tab is dead and you open
the seller's next one yourself.

## Measured facts this design rests on

All verified on macOS with real Chrome, 2026-08-12:

1. **Attaching does not set the automation flag.** Chrome launched by hand with
   `--remote-debugging-port` and connected to via CDP reports
   `navigator.webdriver === false`. The flag comes from `--enable-automation`,
   which we simply do not pass.
2. **The anti-throttling flags do not change that.** Adding
   `--disable-background-timer-throttling`,
   `--disable-backgrounding-occluded-windows` and
   `--disable-renderer-backgrounding` still gives `webdriver === false`.
   This matters — see *Presence* below.
3. **DOM reads work on tabs we never opened, including background ones.** Two
   tabs opened before the tool existed; a giveaway appearing in each was
   detected in the same poll, including in the unpainted background tab.
4. **WebSocket frames do NOT work that way.** A socket already open when we
   attach is invisible to both Playwright's `websocket` event and raw CDP
   `Network.enable`. Zero frames. This is why the design reads the DOM instead
   of the socket, and it is what makes "open your tabs first" possible.

## Presence — the thing that must not break

Both wins so far came from a tab being held open through the draw. Presence is
the product; detection is just how you learn about it.

Chrome throttles background tabs to roughly one timer tick per minute after
five minutes. Whatnot's channel heartbeat is timer-driven. **A hand-launched
Chrome would therefore risk silently dropping presence** — the failure would be
invisible: tabs still open, no errors, entries quietly not counted.

So the launcher must pass the three keep-alive flags above. Fact 2 says this
costs nothing in disguise. This is non-negotiable and should be asserted at
startup, not assumed.

## Components

### 1. `launch.py` — start the browser

Starts Chrome with:

```
--remote-debugging-port=<port>        default 9222
--user-data-dir=<dedicated profile>
--disable-background-timer-throttling
--disable-backgrounding-occluded-windows
--disable-renderer-backgrounding
--no-first-run --no-default-browser-check
```

Explicitly **not** `--enable-automation`, and no stealth or spoofing of any
kind.

Then it verifies, and refuses to continue if wrong:

- the debug port answers
- `navigator.webdriver` is `false`
- a background tab's timers are not throttled

Profile: a dedicated one you log into once and then use normally, so it ages
like a real profile. Fresh profiles were challenged fastest in testing.

> **Open question 1.** Recent Chrome ignores `--remote-debugging-port` when
> pointed at the *default* profile directory. Needs verifying on the target
> machine; if true, a dedicated profile is mandatory rather than merely
> preferred.

### 2. `watch.py` — the watcher

Attaches over CDP, then every `poll_seconds` (default 2):

- enumerate pages; keep those matching `whatnot.com/live/`
- for each, one `page.evaluate` returning a small struct (below)
- diff against the previous reading; notify on transitions
- pages that vanish are dropped; new ones are picked up automatically

No writes to any page. No navigation. Ever.

**Per-tab reading:**

```js
{
  seller, streamTitle,        // from the page header
  giveaway: {
    present: bool,
    prize: string,            // prize name if shown
    entries: number|null,     // "Giveaway with 37 entries"
    buyersOnly: bool,         // if the page distinguishes it
    followersOnly: bool
  }
}
```

**State machine per tab:** `absent → present(signature) → absent`. Notify on
entry to `present`. Signature is `(stream url, prize, buyersOnly)` — there is
no giveaway id in the DOM, so a chained second giveaway with a different prize
reads as new, and the same one re-read does not. Require two consecutive polls
before notifying, to ride out a mid-render frame.

### 3. Purchases / wins

A tab on `whatnot.com/?activityTab=purchases` is read the same way: the list of
rows, each keyed by whatever identifies it (item, seller, timestamp). Rows not
in `state.json` are new → notify. This also replaces the current
`product_sold` detection that arms buyers-only eligibility.

> **Open question 2.** Does that page update by itself when something lands, or
> only on reload? If it updates live, this costs nothing. If not, we reload one
> tab every few minutes — still tiny, but no longer literally zero navigation,
> and it must then be an explicit opt-in setting.

### 4. Notifications

Reuse `NtfyNotifier` / `BarkNotifier` from wn-monitor-web unchanged, including
the tiers already tuned: 🎁 giveaway at `max`, 🏆 win, nothing critical, no
30-second ring, grouped stacks, buyers-only suppressed unless eligible.

### 5. `probe` command

`python watch.py probe` dumps, for each attached tab, the candidate text and
the selectors' current hit/miss. **This is not a debugging luxury — it is how
the tool survives Whatnot changing their markup.** DOM selectors are the
fragile part of this design; probe output is what makes a fix a config edit
instead of a code change.

Selectors live in `selectors.json`, not in code, for the same reason.

## What it deliberately does not do

- **Never enters a giveaway.** Detection and notification only, as now.
- **Never navigates, opens, closes or reloads** — except the purchases tab if
  open question 2 forces it, and then only as an opt-in.
- **No fingerprint spoofing.** It does not pass `--enable-automation`; it also
  does not mask anything. Not passing a flag is not the same as lying.
- **No challenge solving or bypass.** If a tab shows a bot challenge: notify
  once, stop reading that tab, leave it alone. Do not reload it, do not touch
  it. Unlike the current radar this does not stop everything, because you are
  driving — one bad tab is yours to close.

## Failure modes to handle explicitly

| Situation | Behaviour |
|---|---|
| Chrome not running / port dead | clear message, retry with backoff, notify once |
| Every tab closed | idle quietly, keep waiting |
| A tab closed mid-poll | drop it, no error |
| Selector matches nothing anywhere for N minutes | notify: "detection may be broken, run probe" |
| Bot challenge in a tab | notify once, skip that tab |
| Duplicate giveaway reading | suppressed by signature + `state.json` |

The "selector matches nothing" alarm matters: the silent failure mode of this
design is that Whatnot changes their DOM and the tool watches politely forever
while nothing is ever detected.

## Reused from wn-monitor-web

Copy, do not rewrite: the notifier classes, `load_config`/`save_state`/
`prune_state`, `log`, the audit log with its UTF-8 fix, and the config locking
helper. Everything else is new.

## Build order

1. **Attach + probe.** Prove the selectors against real streams. No
   notifications yet. This phase answers whether the whole idea works.
2. **Giveaway detection + notifications.** The core.
3. **Purchases/wins**, once open question 2 is answered.
4. **Launcher + small status view.** Which tabs are being watched, last
   detection, whether the keep-alive assertions hold.

## Open questions, collected

1. Does Chrome ignore `--remote-debugging-port` on the default profile?
2. Does the purchases page update live, or need a reload?
3. Exact selectors for: giveaway present, prize name, entry count, buyers-only,
   followers-only. Requires probing a real live stream — phase 1.
4. Does a background tab hold presence for hours with the keep-alive flags?
   Expected yes; only an overnight run proves it.

## Honest assessment

This trades unattended operation for a much quieter footprint. It is a better
fit for how you actually use it — picking sellers with good giveaways and
watching a handful — than the current design, which was built to sweep.

It is not a guaranteed cure for the challenges. It removes `webdriver: true`
and all automated navigation, which are the two loudest signals we know of. It
does not change your IP, and it cannot make a profile look older than it is.
