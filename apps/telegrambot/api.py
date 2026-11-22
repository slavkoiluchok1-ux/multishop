from typing import cast

from django.conf import settings
from django.contrib.auth import login
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import CustomUser
from apps.accounts.views import user_payload
from apps.core.throttles import LoginRateThrottle, UploadRateThrottle

from .client import TelegramAPIClient, TelegramAPIError
from .models import TELEGRAM_CATEGORIES, TelegramLink, default_telegram_preferences
from .services import (
    CONNECT_CODE_TTL,
    issue_connect_code,
    issue_recovery_code,
    revoke_recovery_code,
    unlink_telegram_user,
    verify_recovery_code,
)

CATEGORY_LABELS = {
    "orders": "Замовлення",
    "wallet": "Гаманець",
    "favorites": "Обране",
    "complaints": "Скарги",
    "support": "Підтримка",
    "moderation": "Модерація та акаунт",
    "admin": "Адмінські сповіщення",
}


class TelegramNotConfigured(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Telegram-бот ще не налаштований адміністратором."
    default_code = "telegram_not_configured"


def _status_payload(user: CustomUser):
    link = TelegramLink.objects.filter(user=user).first()
    connected = (
        link is not None and link.is_active and link.chat_id is not None
    )
    return {
        "configured": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME),
        "connected": connected,
        "telegram_username": link.telegram_username if connected and link is not None else "",
        "bot_username": settings.TELEGRAM_BOT_USERNAME,
        "bot_url": (
            f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}"
            if settings.TELEGRAM_BOT_USERNAME
            else ""
        ),
        "prefs": link.prefs if link is not None else default_telegram_preferences(),
        "categories": [
            {"key": key, "label": CATEGORY_LABELS[key]}
            for key in TELEGRAM_CATEGORIES
        ],
    }


class TelegramStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_status_payload(cast(CustomUser, request.user)))


class TelegramLinkView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadRateThrottle]

    def get(self, request):
        return Response(_status_payload(cast(CustomUser, request.user)))

    def post(self, request):
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_BOT_USERNAME:
            raise TelegramNotConfigured
        user = cast(CustomUser, request.user)
        code = issue_connect_code(user)
        deep_link = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={code}"
        return Response(
            {
                "code": code,
                "deep_link": deep_link,
                "expires_in_seconds": int(CONNECT_CODE_TTL.total_seconds()),
            },
            status=status.HTTP_201_CREATED,
        )

    def delete(self, request):
        TelegramLink.objects.filter(user=request.user).delete()
        return Response({"connected": False})


class TelegramUnlinkView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        unlink_telegram_user(request.user)
        return Response({"connected": False})


class TelegramRecoveryRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        username = str(request.data.get("username") or "").strip()
        user = CustomUser.objects.filter(username__iexact=username).first()
        neutral_response = {
            "detail": (
                "Якщо акаунт із таким ніком існує та до нього підключено Telegram, "
                "інструкції надіслано."
            )
        }
        if user is None:
            return Response(neutral_response)

        link = TelegramLink.objects.filter(user=user, is_active=True).first()
        if link is None or link.chat_id is None:
            return Response(neutral_response)
        if not (link.prefs or {}).get("account_recovery", True):
            return Response(neutral_response)
        if not settings.TELEGRAM_BOT_TOKEN:
            return Response(neutral_response)

        code, recovery = issue_recovery_code(user)
        markup = {
            "inline_keyboard": [[{
                "text": "Відхилити та скасувати код",
                "callback_data": f"recovery_revoke:{recovery.pk}",
            }]]
        }
        try:
            TelegramAPIClient(settings.TELEGRAM_BOT_TOKEN).send_message(
                link.chat_id,
                (
                    "<b>Код відновлення MultiShop</b>\n"
                    f"<code>{code}</code>\n"
                    "Код діє 5 хвилин.\n"
                    "Якщо це були не ви, натисніть кнопку нижче."
                ),
                reply_markup=markup,
                parse_mode="HTML",
            )
        except TelegramAPIError:
            revoke_recovery_code(recovery.pk, user)
            return Response(neutral_response)
        return Response(neutral_response)


class TelegramRecoveryVerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        username = str(request.data.get("username") or "").strip()
        code = str(request.data.get("code") or "").strip()
        user = CustomUser.objects.filter(username__iexact=username).first()
        if user is None:
            return Response(
                {"error": "Код недійсний або минув термін дії."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        link = TelegramLink.objects.filter(user=user, is_active=True).first()
        if link is None or not (link.prefs or {}).get("account_recovery", True):
            return Response(
                {"error": "Відновлення через Telegram недоступне."},
                status=status.HTTP_403_FORBIDDEN,
            )

        recovery = verify_recovery_code(code, user)
        if recovery is None:
            return Response(
                {"error": "Код недійсний, відкликаний або минув термін дії."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not user.is_active:
            return Response(
                {"error": "Акаунт вимкнено."},
                status=status.HTTP_403_FORBIDDEN,
            )
        login(request, user)
        return Response(user_payload(user, request))


class TelegramPreferencesView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        prefs = request.data.get("prefs")
        if not isinstance(prefs, dict) or not prefs:
            raise ValidationError({"prefs": "Передайте налаштування категорій."})
        unknown = set(prefs) - set(TELEGRAM_CATEGORIES)
        if unknown or any(not isinstance(value, bool) for value in prefs.values()):
            raise ValidationError(
                {"prefs": "Категорії мають бути відомими, а значення — true або false."}
            )
        link, _ = TelegramLink.objects.get_or_create(user=request.user)
        link.prefs = {**(link.prefs or {}), **prefs}
        link.save(update_fields=["prefs"])
        return Response({"prefs": link.prefs})
