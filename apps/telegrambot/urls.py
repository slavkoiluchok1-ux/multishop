from django.urls import path

from .api import (
    TelegramLinkView,
    TelegramPreferencesView,
    TelegramRecoveryRequestView,
    TelegramRecoveryVerifyView,
    TelegramStatusView,
    TelegramUnlinkView,
)

urlpatterns = [
    path("telegram/link", TelegramLinkView.as_view(), name="api-telegram-link"),
    path("telegram/status", TelegramStatusView.as_view(), name="api-telegram-status"),
    path("telegram/unlink", TelegramUnlinkView.as_view(), name="api-telegram-unlink"),
    path("telegram/prefs", TelegramPreferencesView.as_view(), name="api-telegram-prefs"),
    path("telegram/recovery/request", TelegramRecoveryRequestView.as_view(), name="api-telegram-recovery-request"),
    path("telegram/recovery/verify", TelegramRecoveryVerifyView.as_view(), name="api-telegram-recovery-verify"),
]
