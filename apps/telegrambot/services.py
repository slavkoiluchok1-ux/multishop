"""Безпечне одноразове підключення Telegram-акаунтів."""
import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import CustomUser

from .models import TelegramLink, TelegramLinkCode, TelegramRecoveryCode

CONNECT_CODE_TTL = timedelta(minutes=10)
RECOVERY_CODE_TTL = timedelta(minutes=5)


class TelegramChatAlreadyLinked(Exception):
    """Raised when a Telegram account is already assigned to another site user."""


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def issue_recovery_code(user: CustomUser) -> tuple[str, TelegramRecoveryCode]:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = timezone.now()
    TelegramRecoveryCode.objects.filter(
        user=user,
        used_at__isnull=True,
        revoked_at__isnull=True,
    ).update(revoked_at=now)
    recovery = TelegramRecoveryCode.objects.create(
        user=user,
        code_hash=_code_hash(code),
        expires_at=now + RECOVERY_CODE_TTL,
    )
    return code, recovery


def revoke_recovery_code(recovery_id: int, user=None) -> bool:
    qs = TelegramRecoveryCode.objects.filter(pk=recovery_id, revoked_at__isnull=True)
    if user is not None:
        qs = qs.filter(user=user)
    updated = qs.update(revoked_at=timezone.now())
    return updated > 0


@transaction.atomic
def verify_recovery_code(code: str, user):
    recovery = (
        TelegramRecoveryCode.objects.select_for_update()
        .filter(
            user=user,
            code_hash=_code_hash(code),
            expires_at__gt=timezone.now(),
            used_at__isnull=True,
            revoked_at__isnull=True,
        )
        .first()
    )
    if recovery is None:
        return None
    recovery.used_at = timezone.now()
    recovery.save(update_fields=["used_at"])
    return recovery


def issue_connect_code(user) -> str:
    code = secrets.token_urlsafe(32)
    now = timezone.now()
    TelegramLinkCode.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
    TelegramLinkCode.objects.create(
        user=user,
        code_hash=_code_hash(code),
        expires_at=now + CONNECT_CODE_TTL,
    )
    return code


@transaction.atomic
def connect_telegram_chat(code: str, chat_id: int, telegram_user_id: int, username: str):
    link_code = (
        TelegramLinkCode.objects.select_for_update()
        .select_related("user")
        .filter(
            code_hash=_code_hash(code),
            expires_at__gt=timezone.now(),
            used_at__isnull=True,
        )
        .first()
    )
    if link_code is None:
        return None

    link = TelegramLink.objects.select_for_update().filter(user_id=link_code.user_id).first()
    already_linked = TelegramLink.objects.filter(
        Q(chat_id=chat_id) | Q(telegram_user_id=telegram_user_id)
    )
    if link is not None:
        already_linked = already_linked.exclude(pk=link.pk)
    if already_linked.exists():
        raise TelegramChatAlreadyLinked

    if link is None:
        link = TelegramLink(user=link_code.user)
    link.chat_id = chat_id
    link.telegram_user_id = telegram_user_id
    link.telegram_username = username[:64]
    link.is_active = True
    link.linked_at = timezone.now()
    link.save()
    link_code.used_at = timezone.now()
    link_code.save(update_fields=["used_at"])
    return link.user


def unlink_telegram_user(user) -> None:
    TelegramLink.objects.filter(user=user).update(
        chat_id=None,
        telegram_user_id=None,
        telegram_username="",
        is_active=False,
        linked_at=None,
    )
