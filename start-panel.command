#!/bin/bash
# Double-click this. Sets itself up the first time, then opens the panel.
cd "$(dirname "$0")" || exit 1
say() { printf "\n  %s\n" "$1"; }
die() { printf "\n  %s\n\n  Press Enter to close.\n" "$1"; read -r _; exit 1; }

if [ ! -x .venv/bin/python ]; then
  say "First run — setting up. A minute or two, only once."
  for c in python3.13 python3.12 python3.11 python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c 'import sys; sys.exit(sys.version_info < (3,11))' 2>/dev/null || continue
    rm -rf .venv
    if "$c" -m venv .venv >/dev/null 2>&1 \
       && .venv/bin/python -m pip --version >/dev/null 2>&1; then break; fi
    rm -rf .venv
  done
fi
if [ ! -x .venv/bin/python ] && command -v uv >/dev/null 2>&1; then
  uv venv --python 3.12 --seed .venv >/dev/null 2>&1
fi
[ -x .venv/bin/python ] || die "Couldn't build a Python environment.
  Install Python 3.11+ from https://www.python.org/downloads/ and try again."

if ! .venv/bin/python -c 'import fastapi, playwright, psutil, qrcode' 2>/dev/null; then
  say "Installing components..."
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt \
    || die "Install failed. Check your internet connection."
fi

say "Starting the panel — leave this window open."
exec .venv/bin/python web.py
