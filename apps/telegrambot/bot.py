"""Telegram inline menus and reliable notification outbox delivery."""
import html
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.favorites.models import Favorite
from apps.notifications.models import Notification
from apps.orders.models import Order
from apps.telegrambot.models import (
    TELEGRAM_CATEGORIES,
    TelegramDelivery,
    TelegramLink,
    TelegramRecoveryCode,
)
from apps.wallet.models import FrozenPayout, Wallet

from .client import TelegramAPIClient, TelegramAPIError
from .services import TelegramChatAlreadyLinked, connect_telegram_chat, unlink_telegram_user
from .services import revoke_recovery_code

logger = logging.getLogger(__name__)
ADMIN_DIGEST_CATEGORIES = {"admin_complaints": "complaints", "admin_support": "support"}
CATEGORY_LABELS = {
    "orders": "Замовлення",
    "wallet": "Гаманець",
    "favorites": "Обране",
    "complaints": "Скарги",
    "support": "Підтримка",
    "moderation": "Модерація",
    "admin": "Адмін",
}


def _button(text, callback_data=None, url=None):
    button = {"text": text}
    if callback_data is not None:
        button["callback_data"] = callback_data
    if url is not None:
        button["url"] = url
    return button


def _markup(rows):
    return {"inline_keyboard": rows}


def _site_url(path):
    if not path or not path.startswith("/") or path.startswith("//"):
        return None
    site_url = settings.SITE_URL.rstrip("/")
    if not site_url.startswith("https://"):
        return None
    return f"{site_url}{path}"


def _site_button(path, label="Відкрити на сайті"):
    url = _site_url(path)
    return _button(label, url=url) if url else None


class TelegramBot:
    def __init__(self, client: TelegramAPIClient):
        self.client = client
        self._last_sent_by_chat = {}

    def handle_update(self, update: dict):
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            self._handle_callback(callback)
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        chat_id = chat.get("id")
        telegram_user_id = sender.get("id")
        if not isinstance(chat_id, int) or not isinstance(telegram_user_id, int):
            return
        if chat.get("type") != "private":
            return

        text = str(message.get("text") or "").strip()
        if text.startswith("/start"):
            self._start(
                chat_id,
                telegram_user_id,
                str(sender.get("username") or ""),
                text,
            )
            return
        link = TelegramLink.objects.select_related("user").filter(
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            is_active=True,
        ).first()
        if link is None:
            self.client.send_message(
                chat_id,
                "Спершу підключіть бота з профілю MultiShop.",
            )
            return
        if text == "/stop":
            self._unlink_from_bot(link)
        else:
            self._send_menu(link)

    def _start(self, chat_id: int, telegram_user_id: int, username: str, text: str):
        parts = text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) == 2 else ""
        if code:
            try:
                user = connect_telegram_chat(code, chat_id, telegram_user_id, username)
            except TelegramChatAlreadyLinked:
                self.client.send_message(
                    chat_id,
                    "Цей Telegram-акаунт уже підключено до іншого акаунта MultiShop.",
                )
                return
            if user is not None:
                link = TelegramLink.objects.select_related("user").get(user=user)
                self._send_menu(link, intro="Акаунт підключено.")
                return

        existing = TelegramLink.objects.filter(
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            is_active=True,
        ).first()
        if existing is not None:
            self._send_menu(existing, intro="Бот уже підключений.")
            return
        self.client.send_message(
            chat_id,
            "Щоб підключити бот, у профілі MultiShop створіть код і відкрийте посилання з ним. "
            "Код діє 10 хвилин.",
        )

    def _send_menu(self, link, intro=""):
        text = f"{html.escape(intro)}\n\n" if intro else ""
        text += "<b>MultiShop</b>\nОберіть потрібний розділ:"
        rows = [
            [_button("🔔 Сповіщення", "notifications:0")],
            [
                _button("📦 Мої", "orders:buyer"),
                _button("📦 В мене", "orders:seller"),
            ],
            [_button("💰 Баланс", "balance"), _button("❤️ Обране", "favorites:0")],
            [_button("⚙️ Налаштування", "settings")],
        ]
        if link.user.is_admin_2 or link.user.is_admin_1 or link.user.is_admin_main:
            rows.append([_button("🛡 Адмін", "admin")])
        support = _site_button("/pages/support.html", "🛟 Підтримка")
        if support:
            rows.append([support])
        self.client.send_message(
            link.chat_id,
            text,
            _markup(rows),
            parse_mode="HTML",
        )

    def _unlink_from_bot(self, link):
        unlink_telegram_user(link.user)
        self.client.send_message(link.chat_id, "Telegram від'єднано від MultiShop.")

    def _handle_callback(self, callback):
        callback_id = callback.get("id")
        message = callback.get("message")
        sender = callback.get("from")
        if not isinstance(message, dict) or not isinstance(sender, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict) or chat.get("type") != "private":
            return
        chat_id = chat.get("id")
        sender_id = sender.get("id")
        message_id = message.get("message_id")
        if not all(isinstance(value, int) for value in (chat_id, sender_id, message_id)):
            return
        link = TelegramLink.objects.select_related("user").filter(
            chat_id=chat_id,
            telegram_user_id=sender_id,
            is_active=True,
        ).first()
        if link is None:
            self.client.request(
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": "Спершу підключіть акаунт."},
            )
            return
        action = str(callback.get("data") or "")
        text, keyboard = self._render(link, action)
        try:
            self.client.request(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text[:4000],
                    "parse_mode": "HTML",
                    "reply_markup": keyboard,
                },
            )
        except TelegramAPIError as exc:
            if exc.error_code != 400 or "message is not modified" not in str(exc).lower():
                raise
        self.client.request("answerCallbackQuery", {"callback_query_id": callback_id})

    def _render(self, link, action):
        if action == "menu":
            return self._main_menu(link)
        if action.startswith("recovery_revoke:"):
            recovery_id = int(action.split(":", 1)[1])
            revoked = revoke_recovery_code(recovery_id, link.user)
            text = (
                "<b>Код відновлення скасовано.</b>\nВін більше не діє."
                if revoked
                else "<b>Код уже недійсний.</b>"
            )
            return text, _markup([[_button("← Меню", "menu")]])
        if action == "notifications:read":
            Notification.objects.filter(user=link.user, is_read=False).update(is_read=True)
            Notification.invalidate_unread_cache(link.user_id)
            return self._notifications_screen(link, 0)
        if action.startswith("notifications:"):
            return self._notifications_screen(link, int(action.split(":", 1)[1]))
        if action in ("orders:buyer", "orders:seller"):
            return self._orders_screen(link, action.split(":", 1)[1])
        if action.startswith("order:"):
            return self._order_detail(link, int(action.split(":", 1)[1]))
        if action == "balance":
            return self._balance_screen(link)
        if action.startswith("favorites:"):
            return self._favorites_screen(link, int(action.split(":", 1)[1]))
        if action == "settings":
            return self._settings_screen(link, "Налаштування Telegram")
        if action == "pref:recovery":
            enabled = not (link.prefs or {}).get("account_recovery", True)
            link.prefs = {**(link.prefs or {}), "account_recovery": enabled}
            link.save(update_fields=["prefs"])
            if not enabled:
                TelegramRecoveryCode.objects.filter(
                    user=link.user,
                    used_at__isnull=True,
                    revoked_at__isnull=True,
                ).update(revoked_at=timezone.now())
            return self._settings_screen(link, "Налаштування збережено.")
        if action.startswith("pref:"):
            category = action.split(":", 1)[1]
            if category in TELEGRAM_CATEGORIES:
                link.prefs = {**(link.prefs or {}), category: not (link.prefs or {}).get(category, True)}
                link.save(update_fields=["prefs"])
            return self._settings_screen(link, "Налаштування збережено.")
        if action == "unlink":
            unlink_telegram_user(link.user)
            return "<b>Telegram від'єднано.</b>", _markup(
                [[_button("Підключити з профілю", url=settings.SITE_URL.rstrip("/") + "/pages/profile.html")]]
            )
        if action == "admin":
            return self._admin_screen(link)
        if action.startswith("admin:"):
            return self._admin_screen(link)
        return self._main_menu(link)

    def _main_menu(self, link):
        text, keyboard = self._menu_payload(link)
        return text, keyboard

    def _menu_payload(self, link):
        text = "<b>MultiShop</b>\nОберіть потрібний розділ:"
        rows = [
            [_button("🔔 Сповіщення", "notifications:0")],
            [_button("📦 Мої", "orders:buyer"), _button("📦 В мене", "orders:seller")],
            [_button("💰 Баланс", "balance"), _button("❤️ Обране", "favorites:0")],
            [_button("⚙️ Налаштування", "settings")],
        ]
        if link.user.is_admin_2 or link.user.is_admin_1 or link.user.is_admin_main:
            rows.append([_button("🛡 Адмін", "admin")])
        support = _site_button("/pages/support.html", "🛟 Підтримка")
        if support:
            rows.append([support])
        return text, _markup(rows)

    def _notifications_screen(self, link, page):
        offset = max(0, page) * 10
        notifications = list(
            Notification.objects.filter(user=link.user)
            .order_by("is_read", "-created_at")[offset : offset + 10]
        )
        if not notifications:
            return "<b>Сповіщення</b>\nНемає сповіщень.", _markup([[_button("← Меню", "menu")]])
        lines = ["<b>🔔 Сповіщення</b>"]
        rows = []
        for item in notifications:
            status_mark = "✓" if item.is_read else "•"
            lines.append(
                f"{status_mark} <b>{html.escape(item.get_section_display())}</b>: "
                f"{html.escape(item.text)}"
            )
            url_button = _site_button((item.payload or {}).get("url"))
            if url_button:
                rows.append([url_button])
        if Notification.objects.filter(user=link.user, is_read=False).exists():
            rows.append([_button("Позначити прочитаними", "notifications:read")])
        if Notification.objects.filter(user=link.user).count() > offset + 10:
            rows.append([_button("Далі →", f"notifications:{page + 1}")])
        rows.append([_button("← Меню", "menu")])
        return "\n\n".join(lines), _markup(rows)

    def _orders_screen(self, link, side):
        field = "buyer_id" if side == "buyer" else "seller_id"
        orders = list(
            Order.objects.filter(**{field: link.user_id})
            .order_by("-created_at")[:10]
        )
        title = "Ваші замовлення" if side == "buyer" else "Замовлення у вас"
        lines = [f"<b>📦 {title}</b>"]
        rows = []
        for order in orders:
            lines.append(
                f"№{order.pk} · {html.escape(order.get_status_display())} · "
                f"{html.escape(str(order.total_mc))} MC"
            )
            rows.append([_button(f"Відкрити №{order.pk}", f"order:{order.pk}")])
        if not orders:
            lines.append("Замовлень поки немає.")
        rows.extend(
            [
                [_button("Мої", "orders:buyer"), _button("В мене", "orders:seller")],
                [_button("← Меню", "menu")],
            ]
        )
        return "\n".join(lines), _markup(rows)

    def _order_detail(self, link, order_id):
        order = Order.objects.filter(
            Q(buyer_id=link.user_id) | Q(seller_id=link.user_id),
            pk=order_id,
        ).first()
        if order is None:
            return "Замовлення не знайдено.", _markup([[_button("← Меню", "menu")]])
        text = (
            f"<b>Замовлення №{order.pk}</b>\n"
            f"Статус: {html.escape(order.get_status_display())}\n"
            f"Сума: {html.escape(str(order.total_mc))} MC"
        )
        button = _site_button(f"/pages/orders.html?order={order.pk}")
        rows = [[button]] if button else []
        rows.append([_button("← До замовлень", "orders:buyer"), _button("← Меню", "menu")])
        return text, _markup(rows)

    def _balance_screen(self, link):
        wallet = Wallet.objects.filter(user_id=link.user_id).first()
        if wallet is None:
            return "Гаманець ще не створено.", _markup([[_button("← Меню", "menu")]])
        lines = [
            "<b>💰 Баланс MultiCoins</b>",
            f"Доступно: {html.escape(str(wallet.free_balance_mc))} MC",
            f"Утримано: {html.escape(str(wallet.held_mc))} MC",
            f"Заморожено: {html.escape(str(wallet.frozen_mc))} MC",
            f"Застава: {html.escape(str(wallet.deposit_locked_mc))} MC",
            f"Борг: {html.escape(str(wallet.debt_mc))} MC",
        ]
        payouts = FrozenPayout.objects.filter(
            seller_id=link.user_id,
            status="frozen",
            release_at__gte=timezone.now(),
        ).order_by("release_at")[:3]
        for payout in payouts:
            lines.append(
                "Найближче розморожування: "
                f"{html.escape(str(payout.amount_mc))} MC — "
                f"{payout.release_at.strftime('%d.%m.%Y')}"
            )
        button = _site_button("/pages/profile.html", "Відкрити гаманець")
        rows = [[button]] if button else []
        rows.append([_button("← Меню", "menu")])
        return "\n".join(lines), _markup(rows)

    def _favorites_screen(self, link, page):
        favorites = list(
            Favorite.objects.filter(user=link.user)
            .select_related("product")
            .prefetch_related("product__variants")
            .order_by("-created_at")[max(0, page) * 10 : max(0, page) * 10 + 10]
        )
        lines = ["<b>❤️ Обране</b>"]
        rows = []
        for favorite in favorites:
            product = favorite.product
            price_values = [variant.price_mc for variant in product.variants.all()]
            price = min(price_values) if price_values else None
            status_text = (
                "недоступний"
                if product.moderation != "ok"
                else product.get_status_display()
            )
            price_text = f"{price} MC" if price is not None else "ціна не вказана"
            lines.append(
                f"• {html.escape(product.name)} · {html.escape(price_text)} · "
                f"{html.escape(status_text)}"
            )
            button = _site_button(f"/pages/product.html?id={product.pk}", product.name[:40])
            if button:
                rows.append([button])
        if not favorites:
            lines.append("Обраних товарів поки немає.")
        if Favorite.objects.filter(user=link.user).count() > (max(0, page) + 1) * 10:
            rows.append([_button("Далі →", f"favorites:{page + 1}")])
        rows.append([_button("← Меню", "menu")])
        return "\n".join(lines), _markup(rows)

    def _settings_screen(self, link, title):
        lines = [f"<b>⚙️ {html.escape(title)}</b>", "Увімкніть категорії сповіщень:"]
        rows = []
        prefs = link.prefs or {}
        for category in TELEGRAM_CATEGORIES:
            enabled = prefs.get(category, True)
            mark = "✅" if enabled else "⬜"
            lines.append(f"{mark} {CATEGORY_LABELS[category]}")
            rows.append(
                [
                    _button(
                        f"{mark} {CATEGORY_LABELS[category]}",
                        f"pref:{category}",
                    )
                ]
            )
        recovery_enabled = prefs.get("account_recovery", True)
        recovery_mark = "✅" if recovery_enabled else "⬜"
        lines.append(f"{recovery_mark} Коди відновлення акаунта")
        rows.append([
            _button(
                f"{recovery_mark} Відновлення акаунта",
                "pref:recovery",
            )
        ])
        rows.extend(
            [
                [_button("Від'єднати Telegram", "unlink")],
                [_button("← Меню", "menu")],
            ]
        )
        return "\n".join(lines), _markup(rows)

    def _admin_screen(self, link):
        user = link.user
        lines = ["<b>🛡 Адмінські черги</b>"]
        rows = []
        if any(user.has_right(right) for right in ("complaints", "products", "bans")):
            from apps.complaints.admin_api import open_complaints_count

            count = open_complaints_count(user)
            lines.append(f"Нерозглянутих скарг, доступних вам: {count}")
            button = _site_button("/pages/admin/complaints.html", "Відкрити скарги")
            if button:
                rows.append([button])
        if user.has_right("support"):
            from apps.support.admin_api import open_support_count

            count = open_support_count(user)
            lines.append(f"Відкритих звернень, доступних вам: {count}")
            button = _site_button("/pages/admin/support.html", "Відкрити звернення")
            if button:
                rows.append([button])
        if len(lines) == 1:
            lines.append("Для цих черг у вас немає прав.")
        rows.append([_button("← Меню", "menu")])
        return "\n".join(lines), _markup(rows)

    def send_pending_notifications(self):
        now = timezone.now()
        deliveries = list(
            TelegramDelivery.objects.filter(status=TelegramDelivery.STATUS_PENDING)
            .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
            .select_related("notification__user")
            .order_by("created_at")[:100]
        )
        processed_admin_groups = set()
        for delivery in deliveries:
            notification = delivery.notification
            link = TelegramLink.objects.filter(user_id=notification.user_id).first()
            if link is None or not self._delivery_is_allowed(notification, link):
                delivery.status = TelegramDelivery.STATUS_FAILED
                delivery.last_error = "Зв'язок або категорію сповіщень вимкнено."
                delivery.save(update_fields=["status", "last_error"])
                continue
            admin_category = ADMIN_DIGEST_CATEGORIES.get(notification.section)
            if admin_category:
                group_key = (link.pk, admin_category)
                if group_key in processed_admin_groups:
                    continue
                processed_admin_groups.add(group_key)
                group = list(
                    TelegramDelivery.objects.filter(
                        notification__user_id=link.user_id,
                        notification__section=notification.section,
                        notification__kind="admin_digest",
                        status=TelegramDelivery.STATUS_PENDING,
                    ).select_related("notification")[:100]
                )
                self._deliver_admin_digest(link, admin_category, group)
                continue
            text = (
                f"🔔 <b>{html.escape(notification.get_section_display())}</b>\n"
                f"{html.escape(notification.text)}"
            )
            keyboard = None
            button = _site_button((notification.payload or {}).get("url"))
            if button:
                keyboard = _markup([[button]])
            self._deliver(delivery, link, text, keyboard)

    def _delivery_is_allowed(self, notification, link):
        if (
            link is None
            or not link.is_active
            or link.chat_id is None
            or not link.user.is_active
            or not settings.TELEGRAM_BOT_TOKEN
        ):
            return False
        category = {
            "admin_complaints": "admin",
            "admin_support": "admin",
            "products": "moderation",
        }.get(notification.section, notification.section)
        if category not in TELEGRAM_CATEGORIES or not (link.prefs or {}).get(category, True):
            return False
        if link.user.is_banned() and category != "support" and notification.kind not in {
            "user_banned",
            "user_unbanned",
            "user_ban_expired",
        }:
            return False
        return True

    def _deliver_admin_digest(self, link, category, deliveries):
        if not deliveries:
            return
        now = timezone.now()
        sent_at = (link.admin_digest_sent_at or {}).get(category)
        if sent_at:
            from django.utils.dateparse import parse_datetime

            last_sent = parse_datetime(sent_at)
            if last_sent and now < last_sent + timedelta(minutes=5):
                next_time = last_sent + timedelta(minutes=5)
                TelegramDelivery.objects.filter(pk__in=[item.pk for item in deliveries]).update(
                    next_attempt_at=next_time
                )
                return
        title = "Нових скарг" if category == "complaints" else "Нових звернень"
        text = f"🛡 <b>{title}: {len(deliveries)}</b>"
        path = (
            "/pages/admin/complaints.html"
            if category == "complaints"
            else "/pages/admin/support.html"
        )
        button = _site_button(path)
        keyboard = _markup([[button]]) if button else None
        self._deliver(deliveries, link, text, keyboard)
        if all(item.status == TelegramDelivery.STATUS_SENT for item in deliveries):
            link.admin_digest_sent_at = {
                **(link.admin_digest_sent_at or {}),
                category: timezone.now().isoformat(),
            }
            link.save(update_fields=["admin_digest_sent_at"])

    def _deliver(self, delivery_or_group, link, text, keyboard):
        deliveries = (
            delivery_or_group
            if isinstance(delivery_or_group, list)
            else [delivery_or_group]
        )
        last_sent = self._last_sent_by_chat.get(link.chat_id)
        if last_sent is not None:
            delay = 1 - (time.monotonic() - last_sent)
            if delay > 0:
                time.sleep(delay)
        try:
            self.client.send_message(
                link.chat_id,
                text,
                keyboard,
                parse_mode="HTML",
            )
        except TelegramAPIError as exc:
            self._delivery_failed(deliveries, link, exc)
            return
        self._last_sent_by_chat[link.chat_id] = time.monotonic()
        now = timezone.now()
        TelegramDelivery.objects.filter(pk__in=[item.pk for item in deliveries]).update(
            status=TelegramDelivery.STATUS_SENT,
            last_error="",
            sent_at=now,
            next_attempt_at=None,
        )
        for item in deliveries:
            item.status = TelegramDelivery.STATUS_SENT
            item.sent_at = now

    def _delivery_failed(self, deliveries, link, error):
        now = timezone.now()
        if error.error_code == 403:
            TelegramLink.objects.filter(pk=link.pk).update(is_active=False)
            TelegramDelivery.objects.filter(pk__in=[item.pk for item in deliveries]).update(
                status=TelegramDelivery.STATUS_FAILED,
                attempts=5,
                last_error="Користувач заблокував Telegram-бота.",
            )
            logger.warning("Telegram delivery disabled for user %s after 403", link.user_id)
            return
        for delivery in deliveries:
            attempts = delivery.attempts + 1
            if attempts >= 5:
                delivery.status = TelegramDelivery.STATUS_FAILED
                delivery.next_attempt_at = None
            else:
                delay = error.retry_after if error.error_code == 429 and error.retry_after else min(
                    2 ** attempts, 60
                )
                delivery.next_attempt_at = now + timedelta(seconds=delay)
            delivery.attempts = attempts
            delivery.last_error = f"Telegram API error {error.error_code or 'network'}"
            delivery.save(
                update_fields=["status", "attempts", "last_error", "next_attempt_at"]
            )
        logger.warning(
            "Telegram delivery failed for user %s (code=%s, attempts=%s)",
            link.user_id,
            error.error_code,
            max(item.attempts for item in deliveries),
        )
