#!/usr/bin/env bash
# PocketADM one-line installer:
#
#   curl -fsSL https://raw.githubusercontent.com/maxaufknax/pocketadm/main/install.sh | bash
#
# Installs Docker if it is missing, builds and starts PocketADM, and ends with
# a QR code: scan it with the PocketADM app and the phone is signed in. The
# QR carries the address, a one-time pairing code, and the fingerprint of
# this server's own HTTPS key, so the app trusts exactly this server.
#
# How the phone reaches the server:
#   (default)               https://<this server's IP>:8443 with PocketADM's
#                           own certificate, pinned through the QR
#   --domain NAME           https://NAME with a Let's Encrypt certificate
#                           (Caddy; ports 80 and 443 free, DNS pointing here)
#   --behind-proxy URL      you run a reverse proxy already: PocketADM listens
#                           on 127.0.0.1:8090 and the QR uses URL
#
# Options can also come from the environment: POCKETADM_DOMAIN,
# POCKETADM_PUBLIC_URL (with --behind-proxy semantics), POCKETADM_TLS_PORT,
# POCKETADM_DIR, POCKETADM_NONINTERACTIVE=1. Safe to re-run: it updates an
# existing install and keeps its settings (.env) unless you pass new ones.
set -euo pipefail

REPO="${POCKETADM_REPO:-${HELMSMAN_REPO:-https://github.com/maxaufknax/pocketadm.git}}"
DOMAIN="${POCKETADM_DOMAIN:-}"
PROXY_URL="${POCKETADM_PUBLIC_URL:-}"
TLS_PORT="${POCKETADM_TLS_PORT:-8443}"
PORT_SET="${POCKETADM_TLS_PORT:+1}"
HTTP_PORT="${HELMSMAN_PORT:-8090}"
NONINTERACTIVE="${POCKETADM_NONINTERACTIVE:-0}"
if [ -n "${POCKETADM_DIR:-${HELMSMAN_DIR:-}}" ]; then
  DIR="${POCKETADM_DIR:-$HELMSMAN_DIR}"
elif [ -d /opt/helmsman/.git ]; then
  DIR=/opt/helmsman                      # installs from before the rename
else
  DIR=/opt/pocketadm
fi

while [ $# -gt 0 ]; do
  case "$1" in
    --domain)         DOMAIN="${2:-}"; shift 2 ;;
    --domain=*)       DOMAIN="${1#*=}"; shift ;;
    --behind-proxy)   PROXY_URL="${2:-}"; shift 2 ;;
    --behind-proxy=*) PROXY_URL="${1#*=}"; shift ;;
    --port)           TLS_PORT="${2:-}"; PORT_SET=1; shift 2 ;;
    --port=*)         TLS_PORT="${1#*=}"; PORT_SET=1; shift ;;
    --dir)            DIR="${2:-}"; shift 2 ;;
    --dir=*)          DIR="${1#*=}"; shift ;;
    -y|--yes)         NONINTERACTIVE=1; shift ;;
    -h|--help)        sed -n '2,22p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)"; exit 1 ;;
  esac
done

say()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null || die "Please run as root (or install sudo)."
  SUDO="sudo"
else
  SUDO=""
fi

case "$TLS_PORT" in ''|*[!0-9]*) die "--port needs a number, got '$TLS_PORT'." ;; esac

port_busy() {  # is something other than PocketADM listening on TCP port $1?
  command -v ss >/dev/null || return 1
  local listeners
  listeners="$($SUDO ss -ltnpH "( sport = :$1 )" 2>/dev/null || true)"
  [ -n "$listeners" ] || return 1
  # a plain program holds the port
  printf '%s\n' "$listeners" | grep -v -q docker-proxy && return 0
  # docker-proxy holds it for *some* container; ours is fine on a re-run
  $SUDO docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null \
    | grep -E ":$1->" | grep -v -q -E '^(helmsman|pocketadm-caddy) ' && return 0
  return 1
}

primary_ip() {  # the address this machine uses to reach the internet
  ip route get 1.1.1.1 2>/dev/null \
    | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}'
}

# ------------------------------------------------------------------ Docker
if ! command -v docker >/dev/null; then
  say "Docker not found — installing it via get.docker.com …"
  curl -fsSL https://get.docker.com | $SUDO sh
fi
$SUDO docker compose version >/dev/null 2>&1 \
  || die "The Docker Compose plugin is missing — install docker-compose-plugin and re-run."

# ------------------------------------------------------------------ source
if [ -d "$DIR/.git" ]; then
  say "Updating the existing install in $DIR …"
  $SUDO git -C "$DIR" pull --ff-only
else
  say "Downloading PocketADM to $DIR …"
  $SUDO git clone --depth 1 "$REPO" "$DIR"
fi
cd "$DIR"

# ------------------------------------------------------------------ how phones reach it
ENVFILE="$DIR/.env"
if [ -z "$DOMAIN" ] && [ -z "$PROXY_URL" ] && [ -z "$PORT_SET" ] && [ -f "$ENVFILE" ]; then
  say "Keeping the existing settings in $ENVFILE"
else
  if [ -z "$DOMAIN" ] && [ -z "$PROXY_URL" ] && [ "$NONINTERACTIVE" != "1" ] \
      && { : < /dev/tty; } 2>/dev/null; then
    echo
    echo "Does this server have a domain name that points at it (e.g. pocketadm.example.com)?"
    echo "With one, PocketADM gets a Let's Encrypt certificate. Without, it uses its own"
    echo "certificate on port $TLS_PORT, which the app trusts through the pairing QR."
    printf "Domain (leave empty to use the IP address): "
    read -r DOMAIN < /dev/tty || DOMAIN=""
  fi
  IP="$(primary_ip)"
  IP="${IP:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
  {
    echo "# Written by install.sh — edit, then run: docker compose up -d"
    echo "HELMSMAN_PORT=$HTTP_PORT"
    if [ -n "$PROXY_URL" ]; then
      echo "HELMSMAN_TLS_BIND=127.0.0.1"
      echo "POCKETADM_PUBLIC_URL=${PROXY_URL%/}"
    elif [ -n "$DOMAIN" ]; then
      echo "COMPOSE_PROFILES=domain"
      echo "POCKETADM_DOMAIN=$DOMAIN"
      echo "HELMSMAN_TLS_BIND=127.0.0.1"
      echo "POCKETADM_PUBLIC_URL=https://$DOMAIN"
    else
      [ -n "$IP" ] || die "Could not work out this server's IP address. Re-run with --domain or --behind-proxy."
      echo "HELMSMAN_TLS_BIND=0.0.0.0"
      echo "HELMSMAN_TLS_PORT=$TLS_PORT"
      case "$IP" in
        *:*) echo "POCKETADM_PUBLIC_URL=https://[$IP]:$TLS_PORT" ;;
        *)   echo "POCKETADM_PUBLIC_URL=https://$IP:$TLS_PORT" ;;
      esac
    fi
  } | $SUDO tee "$ENVFILE" >/dev/null
  $SUDO chmod 600 "$ENVFILE"
fi

env_value() { $SUDO sed -n "s/^$1=//p" "$ENVFILE" | tail -1; }
PUBLIC_URL="$(env_value POCKETADM_PUBLIC_URL)"
ENV_DOMAIN="$(env_value POCKETADM_DOMAIN)"
ENV_TLS_PORT="$(env_value HELMSMAN_TLS_PORT)"
ENV_TLS_BIND="$(env_value HELMSMAN_TLS_BIND)"

if [ -n "$ENV_DOMAIN" ]; then
  for p in 80 443; do
    if port_busy "$p"; then
      die "Port $p is in use by another program. A domain install needs 80 and 443 for Caddy — or re-run with --behind-proxy https://$ENV_DOMAIN if that program is your reverse proxy."
    fi
  done
  if command -v getent >/dev/null && ! getent ahosts "$ENV_DOMAIN" >/dev/null 2>&1; then
    warn "$ENV_DOMAIN does not resolve yet. Let's Encrypt only issues the certificate once its DNS points here."
  fi
elif [ "$ENV_TLS_BIND" = "0.0.0.0" ] && port_busy "${ENV_TLS_PORT:-8443}"; then
  die "Port ${ENV_TLS_PORT:-8443} is in use by another program. Re-run with --port <free port>."
fi

# ------------------------------------------------------------------ start
say "Building and starting PocketADM (the first build takes a few minutes) …"
$SUDO docker compose up -d --build

say "Waiting for it to answer …"
ok=""
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$HTTP_PORT/api/info" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[ -n "$ok" ] || die "PocketADM did not come up. Look at: docker compose -f $DIR/docker-compose.yml logs helmsman"

PW=$($SUDO docker compose logs helmsman 2>/dev/null \
     | grep -oE 'admin password: \S+' | tail -1 | awk '{print $3}') || true

# ------------------------------------------------------------------ firewall hint
if [ "$ENV_TLS_BIND" = "0.0.0.0" ] && command -v ufw >/dev/null \
    && $SUDO ufw status 2>/dev/null | grep -q "Status: active" \
    && ! $SUDO ufw status 2>/dev/null | grep -qE "^${ENV_TLS_PORT:-8443}(/tcp)? +ALLOW"; then
  warn "ufw is active and does not allow port ${ENV_TLS_PORT:-8443} yet. To reach PocketADM from your phone:"
  echo "    sudo ufw allow ${ENV_TLS_PORT:-8443}/tcp"
fi

# ------------------------------------------------------------------ done
echo
say "PocketADM is running."
echo "   Address:   $PUBLIC_URL"
if [ -n "${PW:-}" ]; then
  echo "   Password:  $PW   (for the browser; change it under More → Server)"
fi
if [ "$ENV_TLS_BIND" = "0.0.0.0" ]; then
  echo "   Browser:   PocketADM uses its own certificate, so a browser warns once. The app does not."
fi
if [ -n "$ENV_DOMAIN" ]; then
  echo "   Domain:    Caddy fetches the certificate for $ENV_DOMAIN on the first visit."
fi
echo
echo "   Get the app: https://apps.apple.com/app/pocketadm/id6790731797"
$SUDO docker exec helmsman python -m server.cli pair --url "$PUBLIC_URL" \
  || warn "Could not create a pairing code. Show one later with: sudo docker exec helmsman python -m server.cli pair"
echo
echo "   Pair another phone later:  sudo docker exec helmsman python -m server.cli pair"
