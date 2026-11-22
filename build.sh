#!/usr/bin/env bash
set -o errexit

export DJANGO_SETTINGS_MODULE=config.settings.prod

python -m pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --noinput
python manage.py migrate --noinput
