# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --create-home app \
    && mkdir -p /app/staticfiles /data/media/public /data/media/private \
    && chown -R app:app /app /data

COPY --chown=app:app requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . /app
COPY --chown=app:app --chmod=755 docker/entrypoint.sh /app/entrypoint.sh

USER app

RUN mkdir -p /tmp/collectstatic-data \
    && chown app:app /tmp/collectstatic-data \
    && DJANGO_SETTINGS_MODULE=config.settings.prod \
    DJANGO_SECRET_KEY=collectstatic-build-placeholder \
    DJANGO_ALLOWED_HOSTS=localhost \
    DATA_DIR=/tmp/collectstatic-data \
    python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
