"""Базові налаштування MultiShop. Спільні для dev і prod."""
import os
from pathlib import Path

# Корінь проєкту (папка multishop/)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dev-only-secret-key-change-me-in-production"
)
DEBUG = False
ALLOWED_HOSTS = ["*"]

# --- Додатки ---
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.telegrambot",
    "apps.policy",
    "apps.wallet",
    "apps.catalog",
    "apps.cart",
    "apps.favorites",
    "apps.orders",
    "apps.complaints",
    "apps.support",
    "apps.notifications",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Гейт політики: для залогінених API повертає 403 policy_required
    "apps.policy.middleware.PolicyGateMiddleware",
    # Гейт бану: забанений бачить лише Головну, Підтримку, Політику
    "apps.accounts.middleware.BanGateMiddleware",
    # Гейт тимчасового паролю: must_change_password → лише зміна паролю, вихід, підтримка
    "apps.accounts.middleware.PasswordChangeGateMiddleware",
    "apps.core.middleware.ApiJsonErrorMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_USER_MODEL = "accounts.CustomUser"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000").strip().rstrip("/")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uk"
TIME_ZONE = "Europe/Kyiv"
USE_I18N = True
USE_TZ = True

# --- Статика й фронтенд ---
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# frontend/ віддається як статика: /static/css/base.css -> frontend/css/base.css
STATICFILES_DIRS = [BASE_DIR / "frontend"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# --- Файли користувачів ---
# Публічні файли (фото товарів, аватари, відгуки)
PUBLIC_MEDIA_ROOT = BASE_DIR / "media" / "public"
# Приватні файли (верифікація, цифрові товари, докази скарг, підтримка)
PRIVATE_MEDIA_ROOT = BASE_DIR / "media" / "private"

MEDIA_URL = "/media/"
MEDIA_ROOT = PUBLIC_MEDIA_ROOT

# До 100 МБ на файл
MAX_UPLOAD_MB = 100
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024

# Білий список розширень (PROJECT_STRUCTURE, розділ 9)
ALLOWED_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "gif"]
ALLOWED_VIDEO_EXTENSIONS = ["mp4", "webm", "mov"]
ALLOWED_DOCUMENT_EXTENSIONS = ["pdf", "txt"]
ALLOWED_ARCHIVE_EXTENSIONS = ["zip", "7z", "rar"]
ALLOWED_DIGITAL_EXTENSIONS = [
    "epub", "mp3", "wav", "ogg", "json", "fbx", "obj",
    "gltf", "glb", "psd", "ttf", "otf", "unitypackage",
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF: сесії + CSRF ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "login": "5/minute",
        "registration": "3/hour",
        "support": "5/hour",
        "complaint": "5/hour",
        "review": "10/hour",
        "upload": "20/hour",
        "activation_code_check": "120/minute",
    },
}

# --- Налаштування сайту за замовчуванням (дублюють SiteSettings) ---
SITE_DEFAULTS = {
    "commission_percent": "3",
    "mc_to_uah": "10",
    "deposit_percent": "3",
    "max_upload_mb": 100,
    "max_decimals": 6,
    "payout_freeze_days": 14,
    "complaint_deadline_days": 14,
    "false_complaints_limit": 3,
    "complaints_block_first_days": 30,
    "complaints_block_repeat_days": 365,
}

LOGIN_URL = "/pages/login.html"
