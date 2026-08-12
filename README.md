# Whatnot Giveaway Watcher

Watches the Whatnot tabs **you** opened and buzzes your phone when a giveaway
starts. It never opens, closes, reloads or navigates anything.

It does **not** enter giveaways. That is Whatnot's rule and this tool does not
bend it.

## Why this exists

The automated radar announced itself. Measured on the same machine:

| | radar | this |
|---|---|---|
| `navigator.webdriver` | true | **false** |
| automated page loads | ~6 per 5 min | **zero** |
| requests to Whatnot | discovery + every tab load | **zero** |

The trade: you open the tabs. Every show is a new URL, so when a stream ends
its tab is dead and you open the seller's next one yourself.

## Setup, once

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python launch.py            # creates config.json, opens Chrome
```

Put your Bark key (or ntfy topic) in `config.json`, then:

```bash
.venv/bin/python watch.py test        # your phone should buzz
```

On Windows use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.

## Using it

1. **`python launch.py`** — opens the browser this tool watches. Log in to
   Whatnot the first time. It stays logged in after that.
2. **Open the streams you want**, plus optionally
   `whatnot.com/?activityTab=purchases` for win alerts.
3. **`python watch.py`** — attaches and starts watching. Leave it running.

Your tabs are yours. Closing one just means it stops being watched; opening
another means it starts, no restart needed.

## When it stops detecting things

This reads the words on the page, so it breaks if Whatnot rewords them. That
is the known weak point, and there is a tool for it:

```bash
python watch.py probe
```

That prints, for every open tab, what each pattern currently matches and the
first part of the page text. Fix the regex in **`selectors.json`** — no code
change. If you cannot see what to change, send the probe output.

It also warns you by itself: if nothing matches on any tab for 45 minutes
while streams are open, you get a push saying the selectors may be stale.
Silence is the dangerous failure here, so it is made loud.

## What is confirmed and what is a guess

`Giveaway with N entries` is **known real** — the old radar matched it
successfully for weeks. Everything else in `selectors.json` — the prize name,
buyers-only, followers-only, and the purchases rows — is a **guess** until
probed against a live stream. Wrong guesses degrade gracefully: you still get
the giveaway alert, just with less detail.

## Notifications

| You see | It means |
|---|---|
| 🎁 **prize name** | a giveaway just started — tap, enter by hand |
| 🏆 **You won!** | a new row appeared on your purchases tab |
| ⚠️ **Challenge on a tab** | Cloudflare check in one tab; it is skipped, not touched |
| ⚠️ **Watcher may be blind** | nothing has matched for a long time — run probe |

Buyers-only giveaways are logged but not pushed, since you cannot enter them
without buying in that show.

## Keeping your entries counted

Your entry stays live only while the tab stays open. Chrome throttles
background tabs after a few minutes, which would quietly stop that — so
`launch.py` passes the three flags that prevent it, and then checks they
worked. If it reports a problem, believe it: the failure is invisible
otherwise.

Don't minimise the window on Windows. Windows minimises by parking a window
at an off-screen coordinate that pages can read, and that drew challenges in
testing. Behind other windows, or on a second desktop, is fine.

## Tests

```bash
python test_watcher.py
```

stdlib `unittest`, no extra dependency, under a second.

## Files

| | |
|---|---|
| `launch.py` | starts the browser, and verifies it looks ordinary |
| `watch.py` | attaches, reads tabs, notifies. `watch`, `probe`, `test` |
| `selectors.json` | **the fragile part** — patterns, editable by hand |
| `notify.py` | Bark / ntfy |
| `config.json` | your key and intervals (gitignored) |
| `SPEC.md` | the design, and what was measured to justify it |
