"""Централізоване створення сповіщень сайту та Telegram outbox."""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone

from .models import Notification

logger = logging.getLogger(__name__)

TELEGRAM_SECTION_CATEGORY = {
    "orders": "orders",
    "wallet": "wallet",
    "favorites": "favorites",
    "complaints": "complaints",
    "support": "support",
    "products": "moderation",
    "moderation": "moderation",
    "admin": "admin",
    "admin_complaints": "admin",
    "admin_support": "admin",
}
BAN_ALLOWED_KINDS = {"user_banned", "user_unbanned", "user_ban_expired"}


def _telegram_is_allowed(notification, link) -> bool:
    user = notification.user
    category = TELEGRAM_SECTION_CATEGORY.get(notification.section)
    if (
        not category
        or not settings.TELEGRAM_BOT_TOKEN
        or not link.is_active
        or link.chat_id is None
        or not user.is_active
        or not (link.prefs or {}).get(category, True)
    ):
        return False
    if user.is_banned() and category != "support" and notification.kind not in BAN_ALLOWED_KINDS:
        return False
    return True


def _create_telegram_delivery(notification_id: int) -> None:
    if not settings.TELEGRAM_BOT_TOKEN:
        return
    from apps.telegrambot.models import TelegramDelivery, TelegramLink

    notification = (
        Notification.objects.select_related("user")
        .filter(pk=notification_id)
        .first()
    )
    if notification is None:
        return
    link = TelegramLink.objects.filter(user_id=notification.user_id).first()
    if link is None or not _telegram_is_allowed(notification, link):
        return
    try:
        TelegramDelivery.objects.get_or_create(notification=notification)
    except DatabaseError:
        logger.exception("Could not enqueue Telegram notification %s", notification_id)


def notify(user, section, kind, text, payload=None, url=None):
    """Create one site notification and enqueue Telegram delivery after commit."""
    if user is None:
        return None
    data = dict(payload) if isinstance(payload, dict) else {}
    if url:
        data["url"] = url
    now = timezone.now()
    duplicate = Notification.objects.filter(
        user=user,
        section=section,
        kind=kind,
        payload=data,
        created_at__gte=now - timedelta(seconds=30),
    ).first()
    if duplicate is not None:
        return duplicate

    notification = Notification.objects.create(
        user=user,
        section=section,
        kind=kind,
        text=str(text)[:255],
        payload=data,
    )
    transaction.on_commit(lambda: _create_telegram_delivery(notification.pk))
    return notification


def notify_admin_digest(category, text, payload, url):
    """Notify only admins whose current rights include the relevant queue."""
    from apps.accounts.models import CustomUser

    section = "admin_complaints" if category == "complaints" else "admin_support"
    admins = CustomUser.objects.filter(
        role__in=(CustomUser.ROLE_ADMIN_2, CustomUser.ROLE_ADMIN_1, CustomUser.ROLE_ADMIN_MAIN),
        is_active=True,
    )
    if category == "complaints":
        from apps.complaints.admin_api import _reviewable_query

        for admin in admins:
            if _reviewable_query(admin).filter(pk=payload.get("event_id")).exists():
                notify(admin, section, "admin_digest", text, payload, url)
        return

    from apps.support.models import SupportTicket

    ticket_id = payload.get("ticket_id") or payload.get("event_id")
    ticket = SupportTicket.objects.filter(pk=ticket_id).first()
    if ticket is None:
        return
    for admin in admins:
        if not admin.has_right("support"):
            continue
        if ticket.request_type == "appeal" and not (admin.is_admin_1 or admin.is_admin_main):
            continue
        notify(admin, section, "admin_digest", text, payload, url)


def notify_admin_rights_changed(user, before, after):
    from apps.accounts.models import AdminRight

    labels = dict(AdminRight.RIGHTS)
    for right in sorted(set(after) - set(before)):
        notify(
            user,
            "moderation",
            "admin_right_added",
            f"Ваше право адміністратора «{labels.get(right, right)}» додано.",
            {"right": right},
            url="/pages/admin/cabinet.html",
        )
    for right in sorted(set(before) - set(after)):
        notify(
            user,
            "moderation",
            "admin_right_removed",
            f"Право адміністратора «{labels.get(right, right)}» знято.",
            {"right": right},
            url="/pages/admin/cabinet.html",
        )
