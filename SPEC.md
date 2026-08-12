# Whatnot Giveaway Watcher — attach mode

**The design and the evidence for it.** Written 2026-08-12 before building,
and updated as building changed it — the *Built* section at the end records
what the spec got wrong, because a design doc that quietly agrees with the
code afterwards is worthless.

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

### 1. `chrome.py` — start the browser

Not a separate command: `watch.py` calls it, so there is one thing to run.
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

Then it verifies and reports loudly if wrong:

- the debug port answers
- `navigator.webdriver` is `false`
- the keep-alive flags are actually on the command line, read from
  `chrome://version`

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

## What Whatnot actually runs — measured 2026-08-13

Read out of a live page's own React state, from `watch.py react`. Whatnot
ships these bot-detection fields to the client, by name:

```
playwright_bot     synthetics_bot     user_agent_is_bot
bot_score          is_allowed_bot     is_verified_bot
```

alongside `kasada_gql_request_protection` and `web_kasada_ready_check_logging`.

Two things follow.

**They use Kasada**, a dedicated anti-bot vendor, not only Cloudflare. Kasada
fingerprints the client and scores it; the "just a moment" pages are the
visible end of something more thorough than an IP reputation check.

**They have a detection field named after Playwright specifically.** The old
radar drove Chrome with `navigator.webdriver === true`, which against a system
carrying a `playwright_bot` flag is about as loud as a signal gets. That is the
best explanation yet for why it kept being challenged while ordinary browsing
from the same machine was fine.

The values were null in the payload we captured, so this says what they
measure, not what score we get. But it is why this tool is built the way it
is: real Chrome, no `--enable-automation`, `navigator.webdriver === false`,
zero automated navigation. Not politeness — the difference between being
scored as a browser and scored as a robot.

It also settles the standing rule against anything that makes traffic "look
human". Against a vendor doing behavioural fingerprinting, a half-hearted
imitation is worse than none: it turns an honest tool into one caught lying.

## Reading the giveaway: what is and is not possible

Settled after three probes, and the answer is no.

The banner folds shut on every new giveaway, and collapsed it contains only
the header row — the prize and the eligibility line are absent from the DOM,
not merely hidden. So `textContent` cannot reach them.

Nor can React. A walk over the giveaway component's entire subtree — 52
components — found no `prize`, `isEligible`, `buyerAppreciation`,
`onlyFollowers` or `entryCount` anywhere in props or hook state. The component
literally named `giveaway` is an SVG icon; the stateful ones nearby hold
auction context. That dump was taken with the banner EXPANDED, which makes it
conclusive: if the data is not structured in React with the panel open, it is
certainly not there with it shut. The prize exists only as rendered text.

Clicking the banner open is therefore the only way to read a title or an
eligibility flag. **Decided 2026-08-13: we do not click.** The feature exists
behind `expand_giveaway`, off by default, and stays off. Roughly seventy
clicks an hour across six tabs, each with no mouse movement before it and
fired the instant a giveaway appears, is not a pattern worth buying a prize
name with — least of all against Kasada.

What this costs, stated plainly: every alert reads "🎁 Giveaway — seller" with
no prize name, and giveaways you cannot enter are not filtered out, because
nothing on screen says so. Seller, stream link and timing are unaffected, and
those are what get you into the draw.

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

## Built

All four phases exist. What the spec got wrong along the way:

**The keep-alive check was specified as "verify timers are not throttled",
which cannot be done at startup.** Timing an interval on the tab that was just
opened false-positives, because the navigation to whatnot.com destroys the
interval mid-count. Timing one on a scratch tab proves nothing, because
throttling only applies to *background* tabs after several minutes — it
reported success even with the flags deliberately stripped. It now reads
`chrome://version` and checks the flags Chrome was actually launched with,
which is a thing that can be known at startup.

**The spec said the panel was optional and probably unnecessary.** It was
built, and the selector editor turned out to be the strongest argument for it:
previewing a pattern against a live page immediately caught the default prize
pattern capturing "with 37 entries" out of "Giveaway with 37 entries". A wrong
prize in a push title is worse than none, so the default now requires a colon
and yields nothing otherwise.

**`\d+` was not good enough for entry counts.** It fails to match "1,234"
outright, so a large giveaway was not merely miscounted but invisible — no
notification at all, and the biggest giveaways are exactly the ones with
formatted numbers. Grouped and abbreviated forms are handled now.

**Chrome is not always in Program Files.** A Windows install without admin
rights lands in LOCALAPPDATA. Reported as "Chrome not found" on a machine
that obviously has Chrome.

## Open questions, collected

1. Does Chrome ignore `--remote-debugging-port` on the default profile?
2. Does the purchases page update live, or need a reload?
3. ANSWERED. Confirmed from screenshots: the prize is the line under the
   count, and the page states eligibility itself — "Not eligible", or "Open
   Mobile App To Enter". Both are only present when the banner is expanded,
   which it is not. See above.
4. Does a background tab hold presence with the keep-alive flags? Expected
   yes; only a real run proves it.

## Honest assessment

This trades unattended operation for a much quieter footprint. It is a better
fit for how you actually use it — picking sellers with good giveaways and
watching a handful — than the current design, which was built to sweep.

It is not a guaranteed cure for the challenges. It removes `webdriver: true`
and all automated navigation, which are the two loudest signals we know of. It
does not change your IP, and it cannot make a profile look older than it is.
