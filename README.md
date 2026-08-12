# Whatnot Giveaway Watcher

**You open the Whatnot streams you care about. This watches them and buzzes
your phone the second a giveaway starts.** Tap the notification, you're in the
stream, you enter.

It's a quieter cousin of the automated radar: that one went looking for streams
by itself, which is what kept setting off Whatnot's bot protection. This one
never clicks anything. It only reads the tabs you already have open.

### The one rule that matters

**It does NOT enter giveaways for you. You always tap and enter yourself.**

Whatnot's rules say entries must be made by a real person, by hand. This tool
only *watches and tells you*. That's on purpose, not a missing feature.

---

## What you need

| What | Why | Cost |
|---|---|---|
| A computer that stays on | It does the watching | — |
| Google Chrome | It opens its own copy of Chrome for you | free |
| A Whatnot account | You log in once, in that Chrome | free |
| **Bark** (iPhone) or **ntfy** (Android) | This is what buzzes your phone | free |
| ~10 minutes, once | Setup | — |

You do **not** need to know how to code.

---

## Part 1 — Install it (once)

1. **Get the files.** Green **Code** button → **Download ZIP** → unzip it.
2. **Double-click the launcher** in the folder:
   - **Mac** → `start-panel.command`
   - **Windows** → `start-panel.bat`

The first run installs what it needs — a couple of minutes — then a window
opens showing the control panel. Every time after that it takes seconds.

**On a Mac**, the first double-click may be refused because the file came from
the internet. Right-click it → **Open** → **Open**. Once only.

**If it says Python is missing**, install it from
[python.org/downloads](https://www.python.org/downloads/) — on Windows tick
*"Add Python to PATH"* — then double-click again.

Leave the black window open while you use it. Closing it shuts the panel down.

## Part 2 — Set it up (once)

### 1. Make your phone buzz

Install **Bark** (iPhone) or **ntfy** (Android) first.

- **Bark**: open it, you'll see an address like `https://api.day.app/AbCdEf123456/`.
  The middle part is your key. Copy it.
- **ntfy**: tap **+**, invent a long weird name like `giveaways-x7k2m9qp4z`.
  Anyone who guesses it can read your notifications, so make it long.

In the panel under **phone alerts**: pick your service, paste the key, **save**,
then **send test**. Your phone should buzz. Don't go on until it does.

### 2. Open the browser

Click **open the browser**. A Chrome window appears — **this one is yours**.
Log in to Whatnot in it. Your normal Chrome, bookmarks and logins are never
touched.

You only log in once. It stays logged in.

### 3. Open some streams, press start

In that Chrome, open the streams you want to watch. They appear in the panel
under **your tabs** within a couple of seconds. Then press **start**.

That's it. Go and do something else.

---

## Using it day to day

- **Open a stream** → it starts being watched, no restart needed.
- **Close a stream** → it stops. Nothing to clean up.
- **Every show is a new page.** When a seller's stream ends, that tab is dead.
  Open their next one when they go live again.

Leave the Chrome window open — that's what keeps your giveaway entries counted.
Put it behind your other windows or on a second desktop if it's in the way.

**Don't minimise it on Windows.** Windows minimises by parking the window at a
position off the edge of the screen, and web pages can read that. It looks like
a robot and drew "checking your browser" pages in testing. Behind other windows
is completely fine.

### What the notifications mean

| You see | It means | Do |
|---|---|---|
| 🎁 **prize name** | A giveaway just started | Tap it, enter in the app |
| 🎁 **giveaway** (no name) | Started, but the banner was collapsed so the title wasn't on screen | Same — tap it |
| 🏆 **You won!** | A free item appeared on your purchases page | Check Whatnot |
| 🛒 **Purchase** | Something you paid for appeared there | — |
| ⚠️ **Challenge on a tab** | Whatnot is showing a robot check in one tab | Close that tab, or solve it yourself |
| ⚠️ **Watcher may be blind** | It hasn't recognised anything for a while | See below |

**Giveaways you can't enter are not pushed.** The page says so itself — a
buyers giveaway you haven't bought into shows *"Not eligible"*, and that one
stays quiet. Anything it can't be sure about, it tells you about: a missed
giveaway costs an entry, an extra buzz costs nothing.

**The prize name needs the banner open.** Collapsed, the giveaway box in the
top right shows only "Giveaway with N entries" — the title isn't on screen, so
the alert just says "giveaway". Click the banner open in a stream and the name
comes through. It also tries to read the title straight out of the page even
when collapsed, which often works.

**Wins come from the purchases tab.** Open
`whatnot.com/?activityTab=purchases` in that browser and leave it there. A
giveaway win costs €0.00, which is how it's told apart from something you
bought — more reliable than looking for the word "giveaway", which paid
listings use too. It only notices new rows *after* you start it, so your
history stays quiet.

**Sound is muted.** Six live streams at once is unbearable otherwise. Whatnot
can't tell — the mute is applied inside Chrome, below anything a web page can
see. Set `mute_audio` to `false` in `config.json` if you want it back.

---

## When it stops noticing giveaways

This works by reading the **words on the page**. If Whatnot rewords them, it
stops recognising a giveaway — and the dangerous part is that nothing looks
broken. So it tells you: if nothing has matched for 45 minutes while you have
streams open, you get a push saying it may be blind.

Fixing it is a two-minute job in the panel, under **detection**:

1. Have a stream open with a giveaway running.
2. Press **show me the page text** and find the words the page actually uses.
3. Edit the **giveaway running** box to match, press **try** — it tells you
   straight away whether it matched on your open tabs.
4. Press **save**, then stop and start the watcher.

If you can't see what to change, send that page text and I can.

---

## Using it from your phone

The **use on your phone** card walks you through it and shows a QR code. Short
version: install [Tailscale](https://tailscale.com) on the computer and the
phone, sign in to both with the same account, click **allow my phone**, then
restart the panel.

You still have to be at the computer to open streams, so this is mainly for
checking on it and pressing stop.

**Don't put the panel on the open internet.** Tailscale keeps it reachable only
from your own devices, which is what you actually want.

---

## Honest expectations

- **You do the opening.** No discovery, no rotation. That's the trade for a
  tool that makes no requests to Whatnot at all.
- **Winning is luck.** Popular giveaways have 50+ entrants. This just gets you
  in on time.
- **It can break.** It relies on how Whatnot's page reads today.
- **It's quieter, not invisible.** It removes the two loudest signals — a
  browser that announces itself as automated, and pages loading on their own.
  It cannot change your IP or make a new account look old.

## Your privacy

Everything stays on your computer. Your login, settings and logs are never sent
anywhere. The only things it talks to are Whatnot and your own notification
app.

If you share this folder, don't include `config.json` or `chrome-profile/` —
they hold your login and your notification key. Both are already excluded from
git.

## For technical users

- `python watch.py` — one command: starts the browser if needed, attaches,
  watches. `probe` dumps what it sees, `test` sends a notification.
- `python control.py start|stop|status` — run it without the panel.
- `python test_watcher.py` — 23 tests, stdlib `unittest`, under a second.
  Every case is a defect that actually happened.
- `selectors.json` — all detection, editable by hand or from the panel.
- The panel binds `config.panel_host` (default loopback) on port 8766.
- Reads the DOM, not the WebSocket: a socket already open when it attaches is
  invisible, so reading the page is what lets you open tabs first. See
  `SPEC.md` for what was measured.
- Deliberately unsupported: entering giveaways, and fingerprint spoofing. It
  doesn't pass `--enable-automation`; it also doesn't mask anything.

| file | |
|---|---|
| `watch.py` | reads your tabs and notifies. `watch`, `probe`, `test` |
| `chrome.py` | finds and starts the browser, and checks it looks ordinary |
| `web.py` | the panel |
| `control.py` | start/stop without the panel |
| `notify.py` | Bark / ntfy |
| `selectors.json` | **the fragile part** — all detection |
| `SPEC.md` | the design, what was measured, and what the spec got wrong |
