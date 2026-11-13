#!/usr/bin/env bash
# Nightly backup of the Lekka database on a Linux server. Reads DATABASE_URL from .env (MariaDB/MySQL or
# Postgres), writes backups/lekka_YYYYMMDD_HHMM.sql.gz and keeps the last 30 days.
# Cron example (23:30 every day):  30 23 * * * /opt/lekka/backup_db.sh >> /opt/lekka/backups/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups
PY=venv/bin/python; [ -x "$PY" ] || PY=python3

eval "$("$PY" - <<'EOF'
import os, shlex
from urllib.parse import unquote, urlsplit
from dotenv import load_dotenv
load_dotenv(".env")
u = urlsplit(os.environ["DATABASE_URL"])
kind = "pg" if u.scheme.startswith("postgres") else "mysql"
vals = {"KIND": kind, "DB_HOST": u.hostname or "localhost", "DB_PORT": str(u.port or (5432 if kind == "pg" else 3306)),
        "DB_USER": unquote(u.username or ""), "DB_PASS": unquote(u.password or ""), "DB_NAME": u.path.lstrip("/")}
print("\n".join(f"{k}={shlex.quote(v)}" for k, v in vals.items()))
EOF
)"

OUT="backups/lekka_$(date +%Y%m%d_%H%M).sql.gz"
if [ "$KIND" = "pg" ]; then
  PGPASSWORD="$DB_PASS" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" --no-owner "$DB_NAME" | gzip > "$OUT"
else
  DUMP=$(command -v mariadb-dump || command -v mysqldump)
  MYSQL_PWD="$DB_PASS" "$DUMP" -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" --single-transaction --routines "$DB_NAME" | gzip > "$OUT"
fi
find backups -name 'lekka_*.sql.gz' -mtime +30 -delete
echo "$(date '+%F %T') backup written to $OUT ($(du -h "$OUT" | cut -f1))"
