# Whatnot Giveaway Watcher

**You open the Whatnot streams you care about. This watches them and buzzes
your phone the second a giveaway starts.** Tap the notification, you're in the
stream, you enter.

It never clicks anything on Whatnot. It only reads the tabs you already have
open, straight off the stream's own data — so it's far quieter than a bot that
goes looking for streams by itself.

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
   (Or `git clone https://github.com/stolemynikes/wn-watcher.git`.)
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

In the panel under **phone alerts**: pick your service, paste the key, put in
**your Whatnot username**, **save**, then **send test**. Your phone should
buzz. Don't go on until it does.

The username matters: the win, your entry status, and who won a draw all come
straight off the stream keyed to your name.

### 2. Open the browser

Click **open the browser**. A Chrome window appears — **this one is yours**.
Log in to Whatnot in it. Your normal Chrome, bookmarks and logins are never
touched. You only log in once; it stays logged in.

### 3. Open some streams, press start

In that Chrome, open the streams you want. They appear in the panel under
**your tabs** within a couple of seconds. Then press **start**. That's it.

---

## Using it day to day

- **Open a stream** → it starts being watched, no restart needed.
- **Close a stream** → it stops. Or close it from the panel (the **×** on each
  tab), or all at once with **close all tabs**.
- **Every show is a new page.** When a seller's stream ends, that tab is dead.
  It gets closed automatically (see below) — open their next one when they go
  live again.

Leave the Chrome window open — that's what keeps your giveaway entries counted.
Put it behind your other windows or on a second desktop if it's in the way.

**Don't minimise it on Windows.** Windows minimises by parking the window off
the edge of the screen, and pages can read that — it looks like a robot. Behind
other windows is completely fine.

### What the notifications mean

```
🎁 Giveaway — danihagebeuktcg 🎁
🎁 FREE BOOSTER PACK + FREE SHIPPING 📦 #29
Followers only
Open the app to enter.
```

Seller on top, prize under it, entry rule, and the link opens that stream.

| You see | It means | Do |
|---|---|---|
| 🎁 **Giveaway — seller** | A giveaway just started there | Tap it, enter in the app |
| 🏆 **You won — seller!** | Your name was drawn, or a free item hit your purchases | Tap it — it opens the stream you won in |
| ⚠️ **Challenge on a tab** | Whatnot is showing a robot check in one tab | Close that tab, or solve it yourself |
| ⚠️ **Dropped during a giveaway** | The stream's own feed dropped you while a giveaway was live | Reopen that tab |
| 🔄 **Watcher auto-restarted** | The watcher froze and restarted itself | Nothing — it's back |
| ⚠️ **Watcher can't reach Chrome** | The browser is wedged (usually too many tabs) | Restart Chrome, or let it auto-restart |
| ⚠️ **Watcher may be blind** | It hasn't recognised anything for a while | See *When it stops noticing* |

**Giveaways you can't enter aren't pushed.** If the stream says you're not
eligible, that one stays quiet. Anything it can't be sure about it tells you
about — a missed giveaway costs an entry, an extra buzz costs nothing.

**Domestic-only giveaways from a seller abroad are muted.** "Domestic only"
means entry is limited to the seller's own country. If you're in a different
country you can't join, so those don't buzz you — while *international*
giveaways from the same seller still do. It reads the seller's country from the
stream itself; nothing to configure.

**Sound is muted.** Many live streams at once is unbearable otherwise. Whatnot
can't tell — the mute is inside Chrome, below anything a page can see. Set
`mute_audio` to `false` in `config.json` to get it back.

---

## Two accounts, one for each country

If you have a postal address in another country, you can keep a second Whatnot
account there and switch to it — then *that* country's domestic-only giveaways
become joinable and buzz you.

In the panel, the **account** card lists your accounts. Each is a separate
Chrome profile with its own login. Tap **switch to** and it stops the browser
and watcher, swaps profiles, and starts them again on the other account — log
in the first time you open a new profile. Set each account's Whatnot username
with **set name** (needed for win and presence detection).

The domestic filter flips automatically: on a GB account, UK sellers count as
domestic (they buzz you) and NL sellers as foreign; on an NL account it's the
other way round. It reads each account's own country, so you change nothing.

Accounts live in `config.json` under `accounts` + `active_account`.

---

## Keeping it healthy (RAM, dead tabs, auto-recovery)

A wall of live video tabs is heavy. Left alone, Chrome's memory climbs until
its remote-control port stops answering and the watcher can't read anything.
These keep that from happening — all in `config.json`, all editable from the
panel, all off in a fresh install unless noted:

| Setting | What it does |
|---|---|
| `max_live_tabs` | **The RAM guard.** Ceiling on how many streams stay open. Over it, the oldest stream is closed, keeping the newest — the ones you just opened. Set this to what your machine handles (a laptop ~8–10; a desktop with more memory, higher). `0` = no limit. |
| `close_ended_streams` | When a streamer ends their show, that dead tab is closed for you. |
| `close_duplicate_tabs` | A raid or a double-open leaves two tabs on one stream — one is closed, the other kept. |
| `restart_chrome_on_stuck` | If Chrome's port truly wedges, the watcher restarts Chrome itself. The fresh Chrome opens empty — your old streams are **not** reopened (that reload is exactly what wedged it), so you reopen the few you want. |
| `watchdog_stuck_seconds` | If the watcher's own loop freezes this long, it restarts itself and tells you. |

Because each machine has its own `config.json`, a beefier PC just gets a higher
`max_live_tabs`. If wedges still happen, lower it; the sweet spot is the most
tabs your machine holds without the memory running away.

**In short:** it cleans up dead and duplicate tabs, caps how many run at once,
and if it ever does freeze or lose the browser, it recovers on its own and
pushes you a note.

---

## Using it from your phone

Three ways in, from lightest to fullest. All of them need
[Tailscale](https://tailscale.com) on both the computer and the phone, signed
in to the same account — that's what makes the computer reachable from your
phone anywhere, and only by your own devices.

### 1. The panel, from your phone

In the panel's **use on your phone** card, click **allow my phone**, then
restart the panel. It runs `tailscale serve` and gives you an
`https://<your-computer>.<tailnet>.ts.net` link that opens the full panel on
your phone.

By default the panel asks for a password from anywhere but the computer itself.
On a Tailscale-only tailnet that's already your own devices, so you can drop it:
set `panel_auth` to `false` and the link opens straight in. (That also removes
the password on your home Wi-Fi if the panel is bound to `0.0.0.0` — set
`panel_host` to `127.0.0.1` to keep it Tailscale-only.)

### 2. iOS Shortcuts — open and close streams by tapping Share

Build these once in the **Shortcuts** app; they then appear in Whatnot's Share
sheet (and Home Screen). They POST to the panel over your Tailscale link, so
`panel_auth: false` (above) means no login step.

**"Open on Mac"** — share a show from the Whatnot app straight to the computer:

1. New shortcut → add **Get Contents of URL**.
2. URL: `https://<your-computer>.<tailnet>.ts.net/api/open-link`
3. Expand → **Method: POST**, **Request Body: JSON**.
4. Add a field: key `url` (typed), value = the **Shortcut Input** variable.
5. In settings (ⓘ) → **Show in Share Sheet**, accept **URLs**. Name it.

Now: Whatnot app → a stream → **Share** → your shortcut → it opens on the
computer and the watcher picks it up. Works with the app's `/s/…` share links.

**"Close tabs on Mac"** — simpler, no share needed:

1. New shortcut → **Get Contents of URL**.
2. URL: `https://<your-computer>.<tailnet>.ts.net/api/close-tabs`, **Method: POST**.
3. Name it, optionally add to your Home Screen for one tap.

### 3. SSH — start/stop the watcher from your phone or another PC

For pressing **start / stop / status** without the panel. The **remote control
(SSH)** card in the panel builds the exact commands for you and shows how to
switch on the computer's SSH server. In short:

**Turn on the SSH server on the computer** (once):
- **macOS**: System Settings → General → Sharing → **Remote Login** on
  (or `sudo systemsetup -setremotelogin on`).
- **Windows**: it isn't on by default. PowerShell **as Administrator**:
  ```powershell
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
  Start-Service sshd
  Set-Service -Name sshd -StartupType Automatic
  ```

**From a terminal (Mac/Linux/Windows) or another PC**, the command the panel
gives you looks like:
```
ssh you@your-computer.your-tailnet.ts.net 'python control.py start'
```
(On Windows the path is double-quoted for `cmd.exe` — the panel formats it for
whichever machine it's running on.)

**From an iPhone**, use the Shortcuts action **Run Script Over SSH**: put the
host, port 22 and your username in its fields, and paste the panel's *script*
(the part inside the quotes, e.g. `python control.py start`) into the Script
box. One shortcut each for start / stop / status.

You still have to be at the computer to open streams (SSH is for start/stop),
but the iOS Shortcuts above cover opening and closing from the phone.

**Never put the panel on the open internet.** Tailscale keeps it reachable only
from your own devices, which is what you actually want.

---

## When it stops noticing giveaways

Detection reads the stream's own live data and, as a fallback, the words on the
page. If Whatnot changes something and it stops recognising a giveaway, nothing
looks broken — so it tells you: no match for 45 minutes while streams are open
gets you a "may be blind" push.

Fixing it is a two-minute job in the panel under **detection**: open a stream
with a giveaway running, press **show me the page text**, edit the **giveaway
running** box to match the words the page uses, press **try** (it tells you
straight away whether it matched), **save**, then restart the watcher.

If you can't see what to change, send that page text and it can be fixed for
you.

---

## Honest expectations

- **You do the opening.** No discovery, no rotation — the trade for a tool that
  makes essentially no requests to Whatnot of its own.
- **Winning is luck.** Popular giveaways have 50+ entrants. This just gets you
  in on time. There's a read-only tally in `state.json` (`shadowban_stats`) if
  you ever want to check you're actually being counted.
- **It can break.** It relies on how Whatnot's data and page read today.
- **It's quieter, not invisible.** It removes the loudest signals but can't
  change your IP or make a new account look old.

## Your privacy

Everything stays on your computer. Your login, settings and logs are never sent
anywhere. The only things it talks to are Whatnot and your own notification app.

If you share this folder, don't include `config.json` or `chrome-profile*/` —
they hold your login and keys. Both are already excluded from git.

## For technical users

- `python watch.py watch` — starts the browser if needed, attaches, watches.
  `probe` dumps what it sees, `react` checks the React props, `test` sends a
  notification.
- `python control.py start|stop|restart|status` — run it without the panel.
  `close-tabs` (or `close`) closes Whatnot tabs; add `--live` for just streams.
- `python test_watcher.py` — 121 tests, stdlib `unittest`, well under a second.
  Every case is a defect that actually happened.
- `selectors.json` — the DOM-fallback patterns, editable by hand or the panel.
- The panel binds `config.panel_host` (default loopback) on port 8766.
- **Reads the stream's WebSocket**, passively: the giveaway, entry rule, your
  eligibility, the winner and the entrant list all arrive as frames the tab is
  already receiving — nothing of ours is sent. `SPEC.md` has the measurements.
- Self-healing: a resilient attach that retries instead of dying, a watchdog
  that restarts a frozen loop, and an optional Chrome-restart when the debug
  port wedges.
- Deliberately unsupported: entering giveaways, and fingerprint spoofing. It
  doesn't pass `--enable-automation`; it also doesn't mask anything.

| file | |
|---|---|
| `watch.py` | reads your tabs and notifies. `watch`, `probe`, `react`, `test` |
| `chrome.py` | finds/starts the browser, accounts and profiles, tab helpers |
| `web.py` | the panel + its API |
| `control.py` | start/stop/close-tabs without the panel |
| `notify.py` | Bark / ntfy |
| `selectors.json` | the DOM-fallback detection patterns |
| `SPEC.md` | the design, what was measured, and what the spec got wrong |
