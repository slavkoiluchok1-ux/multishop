"""Гейт політики: залогінений без прийнятої політики не користується API."""
from django.http import JsonResponse

from .services import has_accepted_current

# Ці шляхи доступні навіть без прийнятої політики
EXEMPT_PREFIXES = (
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/admin/badges",
    "/api/profile/password",
    "/api/policy",
    "/api/policy/accept",
    "/api/support/guest",
    "/api/site/blocks",
    "/api/catalog",
    "/api/products",
    "/api/complaints/types",
)


class PolicyGateMiddleware:
    """Для /api/ запитів залогінених користувачів повертає 403 policy_required."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not path.startswith("/api/"):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        if any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES):
            return self.get_response(request)

        if not has_accepted_current(user):
            return JsonResponse({"error": "policy_required"}, status=403)

        return self.get_response(request)
