#!/usr/bin/env bash
# Helmsman one-line installer — like `curl … | bash` on a fresh server.
# Installs Docker if missing, deploys Helmsman on port 8090 and prints the
# generated admin password. Safe to re-run (it updates an existing install).
set -euo pipefail

REPO="${HELMSMAN_REPO:-https://github.com/maxaufknax/helmsman.git}"
DIR="${HELMSMAN_DIR:-/opt/helmsman}"
PORT="${HELMSMAN_PORT:-8090}"

say() { printf '\033[1;36m⎈ %s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null; then SUDO="sudo"; else
    echo "Please run as root (or install sudo)."; exit 1
  fi
else
  SUDO=""
fi

if ! command -v docker >/dev/null; then
  say "Docker not found — installing via get.docker.com …"
  curl -fsSL https://get.docker.com | $SUDO sh
fi
if ! docker compose version >/dev/null 2>&1; then
  say "Docker Compose plugin missing — please install docker-compose-plugin."; exit 1
fi

if [ -d "$DIR/.git" ]; then
  say "Updating existing install in $DIR …"
  $SUDO git -C "$DIR" pull --ff-only
else
  say "Cloning Helmsman to $DIR …"
  $SUDO git clone --depth 1 "$REPO" "$DIR"
fi

cd "$DIR"
say "Building and starting (port $PORT) …"
HELMSMAN_PORT="$PORT" $SUDO docker compose up -d --build

say "Waiting for first start …"
for _ in $(seq 1 30); do
  sleep 2
  PW=$($SUDO docker compose logs helmsman 2>/dev/null \
       | grep -oE 'admin password: \S+' | tail -1 | awk '{print $3}') || true
  [ -n "${PW:-}" ] && break
done

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
say "Helmsman is running!"
echo "   Open:      http://${IP:-<server-ip>}:$PORT"
[ -n "${PW:-}" ] && echo "   Password:  $PW   (change it under More → Server)"
[ -z "${PW:-}" ] && echo "   Password:  see: docker compose -f $DIR/docker-compose.yml logs helmsman | grep 'admin password'"
echo "   Tip:       open the URL on your phone and 'Add to Home Screen' — it installs like an app."
