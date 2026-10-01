#!/usr/bin/env bash
# Redeploy after a push. Run on the VPS.
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/gtm-leadgen}"
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
cd "$APP_DIR"
echo "== pulling"
$SUDO git pull --ff-only
echo "== rebuilding"
PROFILE=""
$SUDO docker compose ps --services 2>/dev/null | grep -q caddy && PROFILE="--profile tls"
$SUDO docker compose $PROFILE up -d --build
echo "== health"
for i in $(seq 20); do
  sleep 3
  curl -fsS --max-time 5 http://127.0.0.1:8000/health && { echo; exit 0; }
done
echo "unhealthy after update; recent logs:"; $SUDO docker compose logs --tail 40; exit 1
