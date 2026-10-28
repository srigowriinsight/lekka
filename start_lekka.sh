#!/usr/bin/env bash
# Starts Lekka and (optionally) shares it through a Cloudflare Quick Tunnel.
# Usage:  ./start_lekka.sh                      (local only, http://localhost:8000)
#         ./start_lekka.sh manager@company.com  (also share, only for this email)
set -e
cd "$(dirname "$0")"
source venv/bin/activate
flask db upgrade
flask extend-recurring
waitress-serve --listen=127.0.0.1:8000 --call app:create_app &
SERVER=$!
trap 'kill $SERVER' EXIT
sleep 2
echo "Lekka is running on http://localhost:8000"
if [ -n "$1" ]; then
  echo "Sharing Lekka; send the https://....trycloudflare.com link below to $1 (it changes on every start)."
  cloudflared tunnel --url http://localhost:8000 --allowed-mail "$1"
else
  wait $SERVER
fi
