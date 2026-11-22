"""Гейти: бан і примусова зміна паролю.

Забанений бачить лише Головну, Підтримку й Політику.
Користувач із must_change_password=True (тимчасовий пароль) може лише
змінити пароль, вийти й звернутись у підтримку.
"""
from django.http import JsonResponse

BAN_EXEMPT_PREFIXES = (
    "/api/auth/",
    "/api/policy",
    "/api/site/blocks",
    "/api/support",
    "/api/notifications/counters",
)

# Шляхи, доступні навіть коли потрібно змінити тимчасовий пароль.
PASSWORD_EXEMPT_PREFIXES = (
    "/api/auth/",
    "/api/admin/badges",
    "/api/profile/password",
    "/api/support",
    "/api/policy",
    "/api/site/blocks",
    "/api/notifications/counters",
)


class BanGateMiddleware:
    """Блокує API для забанених користувачів, крім дозволених шляхів."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not path.startswith("/api/"):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        if any(path.startswith(prefix) for prefix in BAN_EXEMPT_PREFIXES):
            return self.get_response(request)

        check = getattr(user, "is_banned", None)
        if callable(check) and check():
            return JsonResponse({"error": "banned"}, status=403)

        return self.get_response(request)


class PasswordChangeGateMiddleware:
    """Блокує API для користувачів із must_change_password=True.

    Тимчасовий пароль від підтримки: користувач може лише змінити пароль,
    вийти й звернутись у підтримку. Решта API повертає 403 password_change_required.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not path.startswith("/api/"):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        if any(path.startswith(prefix) for prefix in PASSWORD_EXEMPT_PREFIXES):
            return self.get_response(request)

        if getattr(user, "must_change_password", False):
            return JsonResponse({"error": "password_change_required"}, status=403)

        return self.get_response(request)
