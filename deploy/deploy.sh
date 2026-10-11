#!/usr/bin/env bash
# Copy the code to a server, build a venv, and (re)start the systemd user service.
# Usage: deploy/deploy.sh user@host      (or set FORAGE_DEPLOY_HOST)
set -euo pipefail
HOST="${1:-${FORAGE_DEPLOY_HOST:-}}"
[ -n "$HOST" ] || { echo "usage: deploy/deploy.sh user@host" >&2; exit 2; }
cd "$(dirname "$0")/.."
rsync -az --delete --exclude .venv --exclude data --exclude __pycache__ --exclude '*.egg-info' --exclude .git ./ "$HOST:forage/"
ssh "$HOST" 'set -e
  cd ~/forage
  # --without-pip so this also works on Debian/Ubuntu hosts lacking python3-venv (no ensurepip); install with the host pip.
  [ -d .venv ] || python3 -m venv --without-pip .venv
  python3 -m pip --python .venv/bin/python install -q .
  mkdir -p ~/.config/systemd/user ~/.config/forage ~/forage-data
  cp deploy/forage.service ~/.config/systemd/user/forage.service
  systemctl --user daemon-reload
  systemctl --user enable forage >/dev/null 2>&1
  systemctl --user restart forage
  sleep 2; systemctl --user is-active forage'
