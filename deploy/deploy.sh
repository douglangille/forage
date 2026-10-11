#!/usr/bin/env bash
# Sync the code to ubik, build the venv, (re)start the user service. Usage: deploy/deploy.sh [host]
set -euo pipefail
HOST="${1:-douglangille@ubik}"
cd "$(dirname "$0")/.."
rsync -az --delete --exclude .venv --exclude data --exclude __pycache__ --exclude '*.egg-info' --exclude .git ./ "$HOST:forage/"
ssh "$HOST" 'set -e
  cd ~/forage
  # ubik has no python3-venv (no ensurepip) and no sudo: make the venv without pip, install with the host pip
  [ -d .venv ] || python3 -m venv --without-pip .venv
  python3 -m pip --python .venv/bin/python install -q .
  mkdir -p ~/.config/systemd/user ~/forage-data
  cp deploy/forage.service ~/.config/systemd/user/forage.service
  systemctl --user daemon-reload
  systemctl --user enable forage >/dev/null 2>&1
  systemctl --user restart forage
  sleep 2; systemctl --user is-active forage'
