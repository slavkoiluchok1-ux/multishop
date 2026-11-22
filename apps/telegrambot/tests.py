import hashlib
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AdminRight, Ban, CustomUser
from apps.notifications.models import Notification
from apps.notifications.services import notify, notify_admin_digest
from apps.support.models import SupportTicket

from .bot import TelegramBot
from .client import TelegramAPIError
from .models import TelegramDelivery, TelegramLink, TelegramLinkCode, TelegramRecoveryCode
from .services import TelegramChatAlreadyLinked, connect_telegram_chat, issue_connect_code


@override_settings(
    TELEGRAM_BOT_TOKEN="test-token",
    TELEGRAM_BOT_USERNAME="multishop_test_bot",
    SITE_URL="https://shop.example",
)
class TelegramLinkTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="telegram-user",
            email="telegram@example.com",
            password="test-password-123",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_api_issues_hashed_one_time_code_and_deep_link(self):
        response = self.client.post("/api/telegram/link", {}, format="json")

        self.assertEqual(response.status_code, 201)
        response_data = response.json()
        code = response_data["code"]
        row = TelegramLinkCode.objects.get(user=self.user)
        self.assertEqual(row.code_hash, hashlib.sha256(code.encode()).hexdigest())
        self.assertIn(f"?start={code}", response_data["deep_link"])
        self.assertGreater(row.expires_at, timezone.now())

    def test_code_links_private_chat_once_and_rejects_reuse(self):
        code = issue_connect_code(self.user)
        linked_user = connect_telegram_chat(code, 7001, 7001, "telegram_name")

        self.assertEqual(linked_user, self.user)
        link = TelegramLink.objects.get(user=self.user)
        self.assertEqual(link.chat_id, 7001)
        self.assertEqual(link.telegram_username, "telegram_name")
        self.assertTrue(link.is_active)
        self.assertIsNotNone(TelegramLinkCode.objects.get().used_at)
        self.assertIsNone(connect_telegram_chat(code, 7001, 7001, "telegram_name"))

    def test_expired_code_and_already_linked_chat_are_rejected(self):
        code = issue_connect_code(self.user)
        TelegramLinkCode.objects.filter(user=self.user).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertIsNone(connect_telegram_chat(code, 7001, 7001, "first"))

        first_code = issue_connect_code(self.user)
        self.assertEqual(connect_telegram_chat(first_code, 7001, 7001, "first"), self.user)
        other_user = CustomUser.objects.create_user(
            username="telegram-other",
            email="telegram-other@example.com",
            password="test-password-123",
        )
        with self.assertRaises(TelegramChatAlreadyLinked):
            connect_telegram_chat(issue_connect_code(other_user), 7001, 7001, "first")

    def test_status_preferences_and_unlink_endpoints(self):
        TelegramLink.objects.create(user=self.user, chat_id=7001, telegram_user_id=7001)
        response = self.client.get("/api/telegram/status")
        self.assertTrue(response.json()["connected"])
        self.assertEqual(
            response.json()["bot_url"],
            "https://t.me/multishop_test_bot",
        )

        response = self.client.patch(
            "/api/telegram/prefs",
            {"prefs": {"wallet": False}},
            format="json",
        )
        self.assertFalse(response.json()["prefs"]["wallet"])

        response = self.client.post("/api/telegram/unlink", {}, format="json")
        self.assertFalse(response.json()["connected"])
        link = TelegramLink.objects.get(user=self.user)
        self.assertFalse(link.is_active)
        self.assertIsNone(link.chat_id)

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_missing_token_reports_unavailable_without_breaking_status(self):
        response = self.client.get("/api/telegram/status")
        self.assertFalse(response.json()["configured"])
        self.assertEqual(self.client.post("/api/telegram/link", {}, format="json").status_code, 503)

    def test_recovery_request_is_neutral_for_existing_and_missing_usernames(self):
        TelegramLink.objects.create(
            user=self.user,
            chat_id=7001,
            telegram_user_id=7001,
        )
        with patch("apps.telegrambot.api.TelegramAPIClient.send_message") as send_message:
            existing = self.client.post(
                "/api/telegram/recovery/request",
                {"username": self.user.username},
                format="json",
            )
            missing = self.client.post(
                "/api/telegram/recovery/request",
                {"username": "not-a-real-account"},
                format="json",
            )

        self.assertEqual(existing.status_code, 200, existing.json())
        self.assertEqual(missing.status_code, 200, missing.json())
        self.assertEqual(existing.json(), missing.json())
        self.assertIn("Якщо акаунт", existing.json()["detail"])
        send_message.assert_called_once()
        self.assertEqual(TelegramRecoveryCode.objects.filter(user=self.user).count(), 1)

    def test_legacy_delete_endpoint_still_removes_link(self):
        TelegramLink.objects.create(user=self.user, chat_id=7001, telegram_user_id=7001)
        response = self.client.delete("/api/telegram/link")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TelegramLink.objects.filter(user=self.user).exists())


@override_settings(TELEGRAM_BOT_TOKEN="test-token", SITE_URL="https://shop.example")
class TelegramNotificationTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="telegram-bot-user",
            email="telegram-bot@example.com",
            password="test-password-123",
        )
        self.link = TelegramLink.objects.create(
            user=self.user,
            chat_id=7010,
            telegram_user_id=7010,
        )
        self.api = Mock()
        self.bot = TelegramBot(self.api)

    @override_settings(SITE_URL="http://localhost:8000")
    def test_menu_does_not_add_invalid_http_url_for_linked_chat(self):
        update = {
            "message": {
                "chat": {"id": 7010, "type": "private"},
                "from": {"id": 7010, "username": "telegram_bot_user"},
                "text": "/start",
            }
        }

        self.bot.handle_update(update)

        self.api.send_message.assert_called_once()
        markup = self.api.send_message.call_args.args[2]
        buttons = [button for row in markup["inline_keyboard"] for button in row]
        self.assertFalse(any(button.get("url", "").startswith("http://") for button in buttons))

    def test_duplicate_callback_edit_is_answered_without_failing_update(self):
        self.api.request.side_effect = [
            TelegramAPIError(400, "Bad Request: message is not modified"),
            {},
        ]
        update = {
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 7010, "username": "telegram_bot_user"},
                "message": {
                    "message_id": 55,
                    "chat": {"id": 7010, "type": "private"},
                },
                "data": "menu",
            }
        }

        self.bot.handle_update(update)

        self.assertEqual(self.api.request.call_count, 2)
        self.assertEqual(self.api.request.call_args_list[-1].args[0], "answerCallbackQuery")

    def test_notify_enqueues_only_for_connected_and_allowed_category(self):
        with self.captureOnCommitCallbacks(execute=True):
            notification = notify(self.user, "orders", "order", "Нове замовлення")
        self.assertTrue(
            TelegramDelivery.objects.filter(notification=notification, status="pending").exists()
        )

        self.link.prefs = {**self.link.prefs, "wallet": False}
        self.link.save(update_fields=["prefs"])
        with self.captureOnCommitCallbacks(execute=True):
            disabled = notify(self.user, "wallet", "refund", "Повернення")
        self.assertFalse(TelegramDelivery.objects.filter(notification=disabled).exists())

    def test_notification_deduplication_uses_kind_and_payload_not_message_text(self):
        first = notify(self.user, "orders", "order_shipped", "Відправлено", {"order_id": 1})
        duplicate = notify(
            self.user,
            "orders",
            "order_shipped",
            "Повторне повідомлення",
            {"order_id": 1},
        )
        different_event = notify(
            self.user,
            "orders",
            "order_received",
            "Отримано",
            {"order_id": 1},
        )

        assert first is not None
        assert duplicate is not None
        assert different_event is not None
        self.assertEqual(first.pk, duplicate.pk)
        self.assertNotEqual(first.pk, different_event.pk)

    def test_notification_delivery_is_html_escaped_and_sent_once(self):
        notification = Notification.objects.create(
            user=self.user,
            section="support",
            text="<script>секрет</script>",
            payload={"url": "/pages/support.html"},
        )
        delivery = TelegramDelivery.objects.create(notification=notification)

        self.bot.send_pending_notifications()
        self.bot.send_pending_notifications()

        self.api.send_message.assert_called_once()
        self.assertIn("&lt;script&gt;", self.api.send_message.call_args.args[1])
        self.assertNotIn("<script>", self.api.send_message.call_args.args[1])
        self.assertEqual(TelegramDelivery.objects.get(pk=delivery.pk).status, "sent")

    def test_403_deactivates_connection_and_retry_limit_is_five(self):
        notification = Notification.objects.create(
            user=self.user,
            section="orders",
            text="Нове замовлення",
        )
        delivery = TelegramDelivery.objects.create(notification=notification)
        self.api.send_message.side_effect = TelegramAPIError(403, "blocked")
        self.bot.send_pending_notifications()
        self.link.refresh_from_db()
        self.assertFalse(self.link.is_active)
        self.assertEqual(
            TelegramDelivery.objects.get(pk=delivery.pk).status,
            TelegramDelivery.STATUS_FAILED,
        )

        self.link.is_active = True
        self.link.save(update_fields=["is_active"])
        second = Notification.objects.create(
            user=self.user,
            section="orders",
            text="Інше замовлення",
        )
        retry_delivery = TelegramDelivery.objects.create(notification=second)
        self.api.send_message.side_effect = TelegramAPIError(500, "temporary")
        for _ in range(5):
            TelegramDelivery.objects.filter(pk=retry_delivery.pk).update(next_attempt_at=None)
            self.bot.send_pending_notifications()
        retry_delivery.refresh_from_db()
        self.assertEqual(retry_delivery.attempts, 5)
        self.assertEqual(retry_delivery.status, TelegramDelivery.STATUS_FAILED)

    def test_admin_digest_groups_pending_events_and_observes_five_minute_limit(self):
        for index in range(2):
            notification = Notification.objects.create(
                user=self.user,
                section="admin_complaints",
                kind="admin_digest",
                text=f"Скарга {index}",
                payload={"event_id": index + 1},
            )
            TelegramDelivery.objects.create(notification=notification)
        self.bot.send_pending_notifications()
        self.api.send_message.assert_called_once()
        self.assertIn("Нових скарг: 2", self.api.send_message.call_args.args[1])

        notification = Notification.objects.create(
            user=self.user,
            section="admin_complaints",
            kind="admin_digest",
            text="Нова скарга",
            payload={"event_id": 3},
        )
        TelegramDelivery.objects.create(notification=notification)
        self.bot.send_pending_notifications()
        self.api.send_message.assert_called_once()

    def test_admin_digest_excludes_appeals_from_admin_two(self):
        admin2 = CustomUser.objects.create_user(
            username="support-admin2",
            email="support-admin2@example.com",
            password="test-password-123",
            role=CustomUser.ROLE_ADMIN_2,
        )
        AdminRight.objects.create(user=admin2, right="support")
        ticket = SupportTicket.objects.create(
            user=self.user,
            request_type="appeal",
            subject="Апеляція",
            desired_outcome="Переглянути рішення",
        )
        notify_admin_digest(
            "support",
            "Нова апеляція",
            {"event_id": ticket.pk},
            "/pages/admin/support.html",
        )
        self.assertFalse(
            Notification.objects.filter(user=admin2, section="admin_support").exists()
        )

    def test_banned_user_keeps_support_and_ban_messages_only(self):
        with self.captureOnCommitCallbacks(execute=True):
            Ban.objects.create(user=self.user, reason="Правило")
            order_notice = notify(self.user, "orders", "order", "Замовлення")
            support_notice = notify(self.user, "support", "support_reply", "Відповідь")

        self.assertFalse(TelegramDelivery.objects.filter(notification=order_notice).exists())
        self.assertTrue(TelegramDelivery.objects.filter(notification=support_notice).exists())
        self.assertTrue(
            TelegramDelivery.objects.filter(
                notification__user=self.user,
                notification__kind="user_banned",
            ).exists()
        )

    def test_rate_limit_response_uses_retry_after(self):
        notification = Notification.objects.create(
            user=self.user,
            section="orders",
            text="Нове замовлення",
        )
        delivery = TelegramDelivery.objects.create(notification=notification)
        before = timezone.now()
        self.api.send_message.side_effect = TelegramAPIError(429, "slow down", retry_after=25)

        self.bot.send_pending_notifications()

        delivery.refresh_from_db()
        self.assertEqual(delivery.attempts, 1)
        self.assertGreaterEqual(
            delivery.next_attempt_at,
            before + timedelta(seconds=24),
        )

    def test_command_without_token_is_disabled_without_api_call(self):
        from django.core.management import call_command
        from io import StringIO

        output = StringIO()
        with override_settings(TELEGRAM_BOT_TOKEN=""):
            call_command("run_telegram_bot", stdout=output)
        self.assertIn("вимкнено", output.getvalue())
