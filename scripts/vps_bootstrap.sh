#!/usr/bin/env bash
# First-time setup on a fresh Ubuntu/Debian VPS (Hostinger and similar).
# Idempotent: safe to re-run. Run as root, or as a sudo-capable user.
#
#   curl -fsSL <raw-url>/scripts/vps_bootstrap.sh | bash
# or, after cloning:
#   sudo bash scripts/vps_bootstrap.sh
set -euo pipefail

REPO="${REPO:-https://github.com/Excergic/gtme-yc-companies-hiring-signal.git}"
APP_DIR="${APP_DIR:-/opt/gtm-leadgen}"
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

say() { printf '\n== %s\n' "$*"; }

say "1/5 base packages"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq ca-certificates curl git >/dev/null

say "2/5 docker"
if ! command -v docker >/dev/null 2>&1; then
  # official convenience script; pins to Docker's own repo
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  $SUDO sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh
else
  echo "  docker already present: $(docker --version)"
fi
if ! docker compose version >/dev/null 2>&1; then
  $SUDO apt-get install -y -qq docker-compose-plugin >/dev/null
fi
echo "  compose: $(docker compose version | head -1)"
$SUDO systemctl enable --now docker >/dev/null 2>&1 || true

say "3/5 code at $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  $SUDO git -C "$APP_DIR" pull --ff-only
else
  $SUDO mkdir -p "$APP_DIR"
  $SUDO git clone --depth 50 "$REPO" "$APP_DIR"
fi

say "4/5 .env"
if [ ! -f "$APP_DIR/.env" ]; then
  $SUDO cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  TOKEN="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  $SUDO sed -i "s|^APP_TOKEN=.*|APP_TOKEN=${TOKEN}|" "$APP_DIR/.env"
  $SUDO sed -i "s|^DATA_DIR=.*|DATA_DIR=/data/data|;s|^OUT_DIR=.*|OUT_DIR=/data/out|" "$APP_DIR/.env"
  $SUDO chmod 600 "$APP_DIR/.env"
  echo "  created .env with a generated APP_TOKEN"
  echo "  STILL TO SET: DEEPLINE_API_KEY (stages 3 and 5), TZ, SCHEDULE_CRON"
else
  echo "  .env already exists - left untouched"
fi

say "5/5 build and start"
$SUDO mkdir -p "$APP_DIR/data" "$APP_DIR/out"
cd "$APP_DIR"
$SUDO docker compose up -d --build

echo
echo "waiting for health..."
for i in $(seq 30); do
  sleep 3
  if curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "healthy."
    curl -sS http://127.0.0.1:8000/health; echo
    break
  fi
  [ "$i" = "30" ] && { echo "did not become healthy; logs:"; $SUDO docker compose logs --tail 40; exit 1; }
done

echo
echo "APP_TOKEN (needed for POST /api/run):"
$SUDO grep '^APP_TOKEN=' "$APP_DIR/.env" | cut -d= -f2
echo
echo "The service listens on 127.0.0.1:8000 only."
echo "To publish it: set DOMAIN and EMAIL in .env, point a DNS A record at this"
echo "host, then:  cd $APP_DIR && sudo docker compose --profile tls up -d"
