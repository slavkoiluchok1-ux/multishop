"""Захищає API-маршрути від HTML-сторінок помилок Django."""
from django.http import JsonResponse

from .exceptions import GENERIC_SERVER_ERROR


class ApiJsonErrorMiddleware:
    """Не віддає HTML-помилки Django клієнтам API, зокрема в режимі DEBUG."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_response(self, request, response):
        if not request.path.startswith("/api/") or response.status_code < 400:
            return response
        if response.headers.get("Content-Type", "").startswith("application/json"):
            return response

        if response.status_code >= 500:
            message = GENERIC_SERVER_ERROR
        elif response.status_code == 404:
            message = "API-маршрут не знайдено."
        else:
            message = "Не вдалося виконати запит."
        return JsonResponse({"error": message}, status=response.status_code)
