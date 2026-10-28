#!/usr/bin/env bash
# Render start command: update the tables, make sure the login exists, top up recurring dates, then serve.
set -e
flask db upgrade
flask ensure-user
flask extend-recurring
exec waitress-serve --listen=0.0.0.0:${PORT:-10000} --threads=4 --call app:create_app
