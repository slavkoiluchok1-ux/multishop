"""Налаштування для розробки."""
from .base import *  # noqa: F401,F403

DEBUG = True

INSTALLED_APPS = INSTALLED_APPS + ["debug_toolbar"]  # noqa: F405

MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware"] + MIDDLEWARE  # noqa: F405

INTERNAL_IPS = ["127.0.0.1", "localhost"]

# Один і той самий локальний ключ переживає перезапуски; production має окремий ключ.
SECRET_KEY = "dev-only-secret-key-change-me"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = False
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:8000", "http://localhost:8000"]

# Швидша перевірка паролів під час розробки
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
