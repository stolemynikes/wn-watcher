#!/usr/bin/env python3
"""Control panel for the watcher.

    python web.py            # then open http://127.0.0.1:8766

Binds whatever config.panel_host says, loopback by default. To reach it from
your phone, put the machine on a private network (Tailscale) and use the
panel's "allow my phone" button. Never expose it to the open internet.

The point of this panel is the selector editor. Detection depends on Whatnot's
wording, which they change without notice, and hand-editing regexes in a JSON
file is a miserable way to recover from that. Here you can see what each
pattern matches against the tab you actually have open, and fix it in place.
"""

import argparse
import getpass
import io
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse, Response
    from pydantic import BaseModel
except ModuleNotFoundError as exc:
    _py = Path(__file__).resolve().parent / ".venv" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    print(f"\n  Missing '{exc.name}'.\n")
    print(f"  Run:  {_py} -m pip install -r requirements.txt\n"
          if _py.exists() else
          "  Run:  pip install -r requirements.txt\n")
    sys.exit(1)

import chrome
import control
import watch

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.json"
EXAMPLE_PATH = PROJECT_DIR / "config.example.json"
SELECTORS_PATH = PROJECT_DIR / "selectors.json"
STATIC = PROJECT_DIR / "static"
SERVE_HOST = "127.0.0.1"

EDITABLE = {"notifier", "bark_key", "ntfy_topic", "ntfy_server", "debug_port",
            "poll_seconds", "confirm_polls", "silence_warning_minutes",
            "watch_purchases", "panel_password", "panel_host"}

app = FastAPI(title="Whatnot Watcher")
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
_PASSWORD_LOCK = threading.Lock()


def load_config() -> dict:
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def ensure_password() -> str:
    """One random password per install. Locked: this is in the auth path, and
    two simultaneous first requests could otherwise each mint one."""
    with _PASSWORD_LOCK:
        cfg = load_config()
        if (pw := cfg.get("panel_password") or ""):
            return pw
        import secrets
        pw = "-".join(secrets.token_hex(2) for _ in range(3))
        cfg["panel_password"] = pw
        save_config(cfg)
        return pw


@app.middleware("http")
async def guard(request, call_next):
    """No password on the machine itself; required from anywhere else."""
    if (request.client.host if request.client else "") in LOOPBACK:
        return await call_next(request)
    import base64
    import hmac
    header = request.headers.get("authorization", "")
    supplied = ""
    if header.startswith("Basic "):
        try:
            supplied = base64.b64decode(header[6:]).decode().split(":", 1)[-1]
        except Exception:
            supplied = ""
    if not hmac.compare_digest(supplied, ensure_password()):
        return JSONResponse({"error": "password required — see the phone card"},
                            status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Watcher"'})
    return await call_next(request)


# --- reading the browser ----------------------------------------------------


def attached_tabs(cfg: dict) -> dict:
    """What the watcher can currently see. Read directly, so the panel is
    truthful even when the watcher itself is stopped."""
    port = int(cfg.get("debug_port", 9222))
    if not chrome.port_open(port):
        return {"browser": False, "tabs": []}
    try:
        pw, browser = watch.attach(port)
    except SystemExit:
        return {"browser": False, "tabs": []}
    sel = json.loads(SELECTORS_PATH.read_text(encoding="utf-8"))
    tabs = []
    try:
        for page in watch.whatnot_pages(browser):
            try:
                info = page.evaluate(watch.PAGE_TEXT)
            except Exception:
                continue
            url, text = info["url"], info["text"]
            entry = {"url": url, "title": info["title"], "kind": "other"}
            if watch.matches(sel["purchases"].get("url_match"), url):
                entry["kind"] = "purchases"
            elif "/live/" in url:
                entry["kind"] = "live"
                entry["reading"] = watch.read_live_tab(text, sel)
            tabs.append(entry)
    finally:
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()
    return {"browser": True, "tabs": tabs}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def api_status():
    cfg = load_config()
    st = control.status(log_lines=1)
    topic = str(cfg.get("ntfy_topic", ""))
    alerts = (bool(cfg.get("bark_key")) if cfg.get("notifier", "bark") == "bark"
              else bool(topic) and "CHANGE-ME" not in topic)
    return {"control": st, "alerts_configured": alerts,
            "browser_port": int(cfg.get("debug_port", 9222)),
            "browser_up": chrome.port_open(int(cfg.get("debug_port", 9222)))}


@app.get("/api/tabs")
def api_tabs():
    return attached_tabs(load_config())


@app.get("/api/log")
def api_log(lines: int = 200):
    if not control.LOG_FILE.exists():
        return {"lines": []}
    return {"lines": control.LOG_FILE.read_text(
        encoding="utf-8", errors="replace").splitlines()[-lines:]}


class StartReq(BaseModel):
    force: bool = False


@app.post("/api/start")
def api_start(req: StartReq):
    return {"message": control.start(force=req.force)}


@app.post("/api/stop")
def api_stop():
    return {"message": control.stop()}


@app.post("/api/browser/start")
def api_browser_start():
    cfg = load_config()
    if chrome.port_open(int(cfg.get("debug_port", 9222))):
        return {"message": "browser already running — left alone"}
    if not chrome.find_chrome():
        raise HTTPException(400, "Google Chrome not found on this machine")
    ok = chrome.ensure_running(cfg)
    return {"message": "browser started — log in and open your streams"
            if ok else "could not start the browser, see the console"}


def _hint(secret: str) -> str:
    secret = str(secret or "")
    return f"••••{secret[-4:]}" if len(secret) >= 4 else ""


@app.get("/api/config")
def api_get_config():
    cfg = dict(load_config())
    bark, topic = cfg.get("bark_key", ""), str(cfg.get("ntfy_topic", ""))
    cfg["bark_key_set"] = bool(bark)
    cfg["bark_key_hint"] = _hint(bark) if bark else ""
    topic_set = bool(topic) and "CHANGE-ME" not in topic
    cfg["ntfy_topic_set"] = topic_set
    cfg["ntfy_topic_hint"] = _hint(topic) if topic_set else ""
    for gone in ("bark_key", "ntfy_topic", "panel_password"):
        cfg.pop(gone, None)
    return cfg


@app.post("/api/config")
def api_set_config(patch: dict):
    if unknown := set(patch) - EDITABLE:
        raise HTTPException(400, f"not editable: {sorted(unknown)}")
    cfg = load_config()
    cfg.update(patch)
    save_config(cfg)
    return {"message": "saved", "restart_needed": control.read_pid() is not None}


@app.post("/api/test-notification")
def api_test():
    import notify
    try:
        notify.make_notifier(load_config()).send(
            "✅ Watcher test ✅", "Tap to open Whatnot.",
            "https://www.whatnot.com", priority="high", group="test")
    except SystemExit as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"send failed: {exc.__class__.__name__}")
    return {"message": "sent — check your phone"}


# --- selectors: the reason this panel exists --------------------------------


@app.get("/api/selectors")
def api_get_selectors():
    return json.loads(SELECTORS_PATH.read_text(encoding="utf-8"))


class SelectorReq(BaseModel):
    section: str
    name: str
    pattern: str


@app.post("/api/selectors/preview")
def api_preview(req: SelectorReq):
    """Try a pattern against the tabs that are open right now.

    The whole point: you see the effect before saving, against the real page,
    so fixing detection is a thirty-second job instead of a guess.
    """
    try:
        re.compile(req.pattern)
    except re.error as exc:
        raise HTTPException(400, f"not a valid pattern: {exc}")
    cfg = load_config()
    port = int(cfg.get("debug_port", 9222))
    if not chrome.port_open(port):
        raise HTTPException(409, "the browser isn't running")
    pw, browser = watch.attach(port)
    out = []
    try:
        for page in watch.whatnot_pages(browser):
            try:
                info = page.evaluate(watch.PAGE_TEXT)
            except Exception:
                continue
            out.append({"url": info["url"],
                        "match": watch.first_group(req.pattern, info["text"])})
    finally:
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()
    return {"results": out}


@app.post("/api/selectors")
def api_save_selector(req: SelectorReq):
    try:
        re.compile(req.pattern)
    except re.error as exc:
        raise HTTPException(400, f"not a valid pattern: {exc}")
    data = json.loads(SELECTORS_PATH.read_text(encoding="utf-8"))
    if req.section not in data or not isinstance(data[req.section], dict):
        raise HTTPException(400, "unknown section")
    data[req.section][req.name] = req.pattern
    tmp = SELECTORS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SELECTORS_PATH)
    return {"message": f"saved {req.section}.{req.name}",
            "restart_needed": control.read_pid() is not None}


@app.get("/api/page-text")
def api_page_text(url: str = ""):
    """The raw visible text of a tab — what you look at when nothing matches."""
    cfg = load_config()
    port = int(cfg.get("debug_port", 9222))
    if not chrome.port_open(port):
        raise HTTPException(409, "the browser isn't running")
    pw, browser = watch.attach(port)
    try:
        for page in watch.whatnot_pages(browser):
            try:
                info = page.evaluate(watch.PAGE_TEXT)
            except Exception:
                continue
            if not url or info["url"] == url:
                return {"url": info["url"], "text": info["text"][:6000]}
        raise HTTPException(404, "no matching tab")
    finally:
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()


# --- phone access, Tailscale, SSH -------------------------------------------


def find_tailscale():
    """which() is not enough: the Windows installer does not add tailscale.exe
    to PATH, and the macOS App Store build hides it inside the .app."""
    import shutil as _shutil
    if (found := _shutil.which("tailscale")):
        return found
    candidates = [r"C:\Program Files\Tailscale\tailscale.exe",
                  r"C:\Program Files (x86)\Tailscale\tailscale.exe",
                  "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                  "/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale",
                  "/usr/bin/tailscale"]
    if (local := os.environ.get("LOCALAPPDATA")):
        candidates.append(str(Path(local) / "Tailscale" / "tailscale.exe"))
    for path in candidates:
        try:
            if Path(path).is_file():
                return path
        except OSError:
            continue
    return None


def tailnet_state() -> dict:
    ts = find_tailscale()
    if not ts:
        return {"host": None, "state": "missing"}
    try:
        out = subprocess.run([ts, "status", "--json"], capture_output=True,
                             text=True, timeout=6)
        me = (json.loads(out.stdout) or {}).get("Self") or {}
        host = (me.get("DNSName") or "").rstrip(".")
        if not me.get("Online") or not host:
            return {"host": None, "state": "signed-out"}
        return {"host": host, "state": "ready"}
    except Exception:
        return {"host": None, "state": "signed-out"}


def _tailscale_serve_command(port: int) -> str:
    exe = find_tailscale() or "tailscale"
    if " " not in exe:
        return f"{exe} serve --bg {port}"
    # A quoted path is only a string in PowerShell until you prefix it with &.
    prefix = "& " if platform.system() == "Windows" else ""
    return f'{prefix}"{exe}" serve --bg {port}'


@app.get("/api/phone-info")
def api_phone_info(request: Request):
    local = (request.client.host if request.client else "") in LOOPBACK
    tailnet = tailnet_state()
    port = request.url.port or 8766
    bound_all = SERVE_HOST != "127.0.0.1"
    wants_all = str(load_config().get("panel_host") or "127.0.0.1") != "127.0.0.1"
    return {"tailscale": bool(tailnet["host"]),
            "tailscale_state": tailnet["state"],
            "url": f"http://{tailnet['host']}:{port}" if tailnet["host"] else None,
            "password": ensure_password() if local else None,
            "bound_all": bound_all,
            "restart_pending": wants_all and not bound_all,
            "launcher": "start-panel.bat" if platform.system() == "Windows"
                        else "start-panel.command",
            "tailscale_serve": _tailscale_serve_command(port),
            "port": port}


class PhoneAccessReq(BaseModel):
    allow: bool


@app.post("/api/phone-access")
def api_phone_access(req: PhoneAccessReq):
    cfg = load_config()
    cfg["panel_host"] = "0.0.0.0" if req.allow else "127.0.0.1"
    save_config(cfg)
    return {"message": "phone access saved — close the panel window and start "
                       "it again to apply" if req.allow else
                       "phone access off — restart the panel to apply"}


@app.get("/api/phone-qr.svg")
def api_phone_qr(request: Request):
    try:
        import qrcode
        import qrcode.image.svg
    except ModuleNotFoundError:
        raise HTTPException(503, "QR support not installed")
    host = tailnet_state()["host"]
    if not host:
        raise HTTPException(404, "Tailscale not running")
    q = qrcode.QRCode(box_size=9, border=2)
    q.add_data(f"http://{host}:{request.url.port or 8766}")
    q.make(fit=True)
    buf = io.BytesIO()
    q.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml")


def ssh_server_state() -> dict:
    """Is anything listening on 22 here, and if not, how to fix it.

    "Could not connect to the SSH server" reads the same whether the address
    is wrong or no server was ever installed — which on Windows is the default
    state. Checking locally rules that out before anyone starts guessing.
    """
    listening = False
    for addr in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((addr, 22), timeout=1):
                listening = True
                break
        except OSError:
            continue
    system = platform.system()
    if system == "Windows":
        how = {"summary": "Windows does not install an SSH server by default.",
               "intro": "Open PowerShell as Administrator and work down in "
                        "order. Each step needs the one above it to have worked.",
               "procedure": [
                   {"text": "Check this window really is elevated — it must "
                            "print True.",
                    "command": "([Security.Principal.WindowsPrincipal]"
                               "[Security.Principal.WindowsIdentity]::GetCurrent())"
                               ".IsInRole([Security.Principal.WindowsBuiltInRole]"
                               "::Administrator)"},
                   {"text": "See whether Windows offers it. Expect State : "
                            "NotPresent or Installed.",
                    "command": "Get-WindowsCapability -Online -Name OpenSSH.Server*"},
                   {"text": "If NotPresent, install it. Error 0x800f0954 means "
                            "the download is blocked — use the winget line below.",
                    "command": "Add-WindowsCapability -Online -Name "
                               "OpenSSH.Server~~~~0.0.1.0"},
                   {"text": "Only now does the sshd service exist.",
                    "command": "Start-Service sshd"},
                   {"text": "Make it come back after a reboot.",
                    "command": "Set-Service -Name sshd -StartupType Automatic"},
                   {"text": "Let it through the firewall.",
                    "command": "New-NetFirewallRule -Name sshd -DisplayName "
                               "'OpenSSH Server (sshd)' -Enabled True -Direction "
                               "Inbound -Protocol TCP -Action Allow -LocalPort 22"},
                   {"text": "ONLY if Add-WindowsCapability failed. The id "
                            "really is Preview — Microsoft renamed it from Beta.",
                    "command": "winget install --id Microsoft.OpenSSH.Preview "
                               "--accept-package-agreements --accept-source-agreements"},
               ],
               "check": "Get-Service sshd should say Running. A Microsoft "
                        "account's username is NOT your email — use whoami."}
    elif system == "Darwin":
        how = {"summary": "macOS has one built in; it only needs switching on.",
               "intro": "No installing required.",
               "procedure": [
                   {"text": "System Settings → General → Sharing → turn on "
                            "Remote Login.", "command": ""},
                   {"text": "Or from a terminal.",
                    "command": "sudo systemsetup -setremotelogin on"}],
               "check": "Remote Login shows a green dot. Use the name from whoami."}
    else:
        how = {"summary": "Install and enable OpenSSH.",
               "intro": "On Debian/Ubuntu:",
               "procedure": [
                   {"text": "Install it.", "command": "sudo apt install openssh-server"},
                   {"text": "Start it, and at boot.",
                    "command": "sudo systemctl enable --now ssh"}],
               "check": "systemctl status ssh should say active (running)."}
    return {"listening": listening, **how}


@app.get("/api/ssh-info")
def api_ssh_info():
    """Two shapes, because they are not interchangeable. A terminal wants one
    `ssh user@host '<command>'` line. Apple's Run Script Over SSH action has
    its own Host/Port/User fields and runs the Script box AS the remote
    command — paste the full ssh line there and it runs ssh on the far end."""
    import shlex
    user = getpass.getuser()
    short = socket.gethostname().split(".")[0]
    hosts = [f"{short}.local" if platform.system() == "Darwin" else short]
    if (name := tailnet_state()["host"]):
        hosts.insert(0, name)

    actions = ("start", "stop", "status")
    parts = [sys.executable, str(PROJECT_DIR / "control.py")]
    if platform.system() == "Windows":
        # Windows sshd hands the command to cmd.exe, which wants double quotes,
        # and these paths contain spaces often enough to matter.
        quoted = " ".join(f'"{p}"' for p in parts)
    else:
        quoted = " ".join(shlex.quote(p) for p in parts)
    scripts = {a: f"{quoted} {a}" for a in actions}

    def outer(script: str) -> str:
        # shlex.quote is always right but renders a spaced path as
        # ''"'"'/My Stuff/python'"'"' …' — correct, and nobody would paste it.
        if "'" not in script:
            return f"'{script}'"
        if not any(c in script for c in '"$`\\'):
            return f'"{script}"'
        return shlex.quote(script)

    return {"user": user, "host": hosts[0], "hosts": hosts, "port": 22,
            "user_warning": (
                f"Your account name ({user}) starts with a dash, and OpenSSH "
                "rejects those — no ssh command can use it. You would need a "
                "different local user." if user.startswith("-") else ""),
            "via": "tailscale" if len(hosts) > 1 else "local network",
            "scripts": scripts,
            "ssh_server": ssh_server_state(),
            "commands": {a: f"ssh {user}@{hosts[0]} {outer(s)}"
                         for a, s in scripts.items()},
            "remote_login_hint": ssh_server_state()["summary"]}


@app.exception_handler(HTTPException)
def http_error(_request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


# --- serving ----------------------------------------------------------------


def open_panel(url: str, app_window: bool = True) -> None:
    """Its own window, not a tab in the browser being watched.

    webbrowser.open asks the OS to hand the URL to a running browser — which
    can be the watched one. Launching Chrome directly avoids that: its
    single-instance routing is keyed on user-data-dir, so with none given this
    reaches your ordinary profile.
    """
    if app_window and (exe := chrome.find_chrome()):
        try:
            subprocess.Popen([exe, f"--app={url}"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    webbrowser.open(url)


def panel_already_running(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def serve(host: str, port: int) -> None:
    import uvicorn
    # timeout_graceful_shutdown is the difference between Ctrl+C working and
    # appearing to hang: the open panel page holds a keep-alive connection and
    # uvicorn otherwise waits for it forever.
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port,
                                           log_level="warning",
                                           timeout_graceful_shutdown=3))
    if platform.system() != "Windows":
        try:
            server.run()
        except SystemExit:
            return print(f"\n  Could not listen on {host}:{port}.\n")
        return print("\n  Panel stopped.\n")

    # Windows: the proactor loop wakes for socket I/O, not signals, so an idle
    # panel swallows Ctrl+C entirely. Handle it ourselves with a heartbeat.
    import asyncio
    import signal
    server.install_signal_handlers = lambda: None

    async def run() -> None:
        def stop(*_):
            if server.should_exit:
                server.force_exit = True
            server.should_exit = True
        for sig in (signal.SIGINT, signal.SIGBREAK):
            signal.signal(sig, stop)
        task = asyncio.ensure_future(server.serve())
        while not task.done():
            await asyncio.sleep(0.2)
        await task

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except SystemExit:
        return print(f"\n  Could not listen on {host}:{port}.\n")
    print("\n  Panel stopped.\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--tab", action="store_true",
                    help="ordinary tab instead of its own window")
    args = ap.parse_args()

    if not CONFIG_PATH.exists():
        save_config(json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")))
        print(f"  Created {CONFIG_PATH.name}")

    # Nobody types a command to start this — they double-click the launcher,
    # which passes no arguments. So the phone-access choice lives in config.
    host = args.host or str(load_config().get("panel_host") or "127.0.0.1")
    global SERVE_HOST
    SERVE_HOST = host
    url = f"http://127.0.0.1:{args.port}"

    if panel_already_running(args.port):
        print(f"\n  The panel is already running:  {url}\n")
        if not args.no_browser:
            open_panel(url, not args.tab)
        return

    if host not in ("127.0.0.1", "localhost"):
        print(f"\n  Reachable on your network ({host}).")
        print(f"  Password for remote access: {ensure_password()}")
        print("  Only on a private network such as Tailscale.")
    print(f"\n  Whatnot Watcher panel:  {url}")
    print("  Leave this window open. Ctrl+C to shut the panel down.\n")
    if not args.no_browser:
        threading.Thread(
            target=lambda: (time.sleep(1.5), open_panel(url, not args.tab)),
            daemon=True).start()
    serve(host, args.port)


if __name__ == "__main__":
    main()
