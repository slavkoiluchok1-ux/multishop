#!/bin/sh
set -eu

mkdir -p "${DATA_DIR:-/data}/media/public" "${DATA_DIR:-/data}/media/private"

python manage.py migrate --noinput

if [ -n "${MAIN_ADMIN_NICK:-}${MAIN_ADMIN_EMAIL:-}${MAIN_ADMIN_PASSWORD:-}" ]; then
    python manage.py create_main_admin
fi

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-2}" \
    --threads 4 \
    --timeout 120
