"""Єдиний формат помилок API."""
import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)
GENERIC_SERVER_ERROR = "Щось пішло не так, спробуйте пізніше"


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        logger.error(
            "Необроблена помилка API",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return Response({"error": GENERIC_SERVER_ERROR}, status=500)
    if response.status_code >= 500:
        response.data = {"error": GENERIC_SERVER_ERROR}
        return response
    if response.status_code == 429:
        response.data = {"error": "Забагато запитів. Спробуйте пізніше."}
        return response
    detail = response.data
    if isinstance(detail, dict) and "detail" in detail:
        response.data = {"error": str(detail["detail"])}
    elif isinstance(detail, dict):
        response.data = {"error": "Невірні дані", "fields": detail}
    elif isinstance(detail, list):
        response.data = {"error": "; ".join(str(x) for x in detail)}
    else:
        response.data = {"error": str(detail)}
    return response
