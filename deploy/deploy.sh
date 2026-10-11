#!/usr/bin/env bash
# Sync the code to ubik, build the venv, (re)start the user service. Usage: deploy/deploy.sh [host]
set -euo pipefail
HOST="${1:-douglangille@ubik}"
cd "$(dirname "$0")/.."
rsync -az --delete --exclude .venv --exclude data --exclude __pycache__ --exclude '*.egg-info' --exclude .git ./ "$HOST:rill/"
ssh "$HOST" 'set -e
  cd ~/rill
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/pip install -q -U pip && .venv/bin/pip install -q .
  mkdir -p ~/.config/systemd/user ~/rill-data
  cp deploy/rill.service ~/.config/systemd/user/rill.service
  systemctl --user daemon-reload
  systemctl --user enable rill >/dev/null 2>&1
  systemctl --user restart rill
  sleep 2; systemctl --user is-active rill'
