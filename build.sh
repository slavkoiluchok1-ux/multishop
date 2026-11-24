#!/usr/bin/env bash
set -o errexit

export DJANGO_SETTINGS_MODULE=config.settings.prod

python -m pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py seed_complaint_types
python manage.py bootstrap_admin
if [ -n "${MAIN_ADMIN_NICK:-}" ] || [ -n "${MAIN_ADMIN_EMAIL:-}" ] || [ -n "${MAIN_ADMIN_PASSWORD:-}" ]; then
  python manage.py create_main_admin
fi
python manage.py bootstrap_seed
python manage.py bootstrap_policy
python manage.py bootstrap_home
