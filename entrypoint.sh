#!/usr/bin/env bash
set -euo pipefail

wait_for_postgres() {
  python - <<'PY'
import os
import sys
import time

url = (os.environ.get("DATABASE_URL") or "").strip()
if not url:
    sys.exit(0)
if not (url.startswith("postgres://") or url.startswith("postgresql://")):
    sys.exit(0)

import psycopg

deadline = time.time() + 120
last_err = None
while time.time() < deadline:
    try:
        conn = psycopg.connect(url, connect_timeout=5)
        conn.close()
        sys.exit(0)
    except Exception as e:
        last_err = e
        time.sleep(2)

print(f"Database not ready after timeout: {last_err}", file=sys.stderr)
sys.exit(1)
PY
}

echo "Waiting for database..."
wait_for_postgres
echo "Database ready"
echo "Running migrations..."
python manage.py migrate --noinput
echo "Migrations done"
echo "Collecting static files..."
python manage.py collectstatic --noinput
echo "Static files ready"
echo "Starting server..."
exec gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000}
