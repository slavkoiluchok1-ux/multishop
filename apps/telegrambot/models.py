"""Telegram account links, connection codes, and delivery outbox."""
from django.conf import settings
from django.db import models


TELEGRAM_CATEGORIES = (
    "orders",
    "wallet",
    "favorites",
    "complaints",
    "support",
    "moderation",
    "admin",
)


def default_telegram_preferences():
    return {category: True for category in TELEGRAM_CATEGORIES}


class TelegramLink(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_link",
    )
    user_id: int

    chat_id = models.BigIntegerField(null=True, blank=True, unique=True)
    telegram_user_id = models.BigIntegerField(null=True, blank=True, unique=True)
    telegram_username = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)
    prefs = models.JSONField(default=default_telegram_preferences)
    admin_digest_sent_at = models.JSONField(default=dict, blank=True)
    linked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Telegram link for {self.user_id}"


class TelegramLinkCode(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_link_codes",
    )
    user_id: int

    code_hash = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "used_at"], name="tg_code_user_used_idx")]


class TelegramRecoveryCode(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_recovery_codes",
    )
    code_hash = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "used_at", "revoked_at"], name="tg_recovery_user_idx"),
        ]


class TelegramDelivery(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Очікує"),
        (STATUS_SENT, "Надіслано"),
        (STATUS_FAILED, "Помилка"),
    )

    notification = models.OneToOneField(
        "notifications.Notification",
        on_delete=models.CASCADE,
        related_name="telegram_delivery",
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="tg_delivery_due_idx"),
        ]


class TelegramBotState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_update_id = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
