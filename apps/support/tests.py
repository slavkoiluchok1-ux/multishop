"""Тести підтримки: гостьове звернення, звернення користувача, адмінка, відновлення.

Гейт політики в тестах не спрацьовує, бо поточної редакції політики немає
(has_accepted_current повертає True). Пул звернень спільний для адмінів із правом.
"""
from typing import Any, cast
from decimal import Decimal
from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponseBase
from django.test import TestCase
from django.test.utils import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AdminLog, AdminRight, Ban, CustomUser
from apps.orders.models import Order, Refund
from apps.wallet.models import CoinIssue, EscrowHold, Wallet, WalletTransaction

from .models import AccountRecovery, SupportAction, SupportMessage, SupportTicket


def api_data(response: HttpResponseBase) -> dict[str, Any]:
    return cast(dict[str, Any], cast(Any, response).data)


class SupportAPITestCase(TestCase):
    api_client: APIClient


def make_user(username: str, role: str = CustomUser.ROLE_USER, **kwargs: Any) -> CustomUser:
    user = CustomUser.objects.create_user(
        username=username, email=f"{username}@example.com", password="pass12345", **kwargs
    )
    if role != CustomUser.ROLE_USER:
        user.role = role
        user.save(update_fields=["role"])
    return user


class SupportGuestTests(SupportAPITestCase):
    def setUp(self):
        cache.clear()
        self.api_client = APIClient()

    def test_guest_creates_ticket_without_login(self):
        account_user = make_user("account_owner")
        response = self.api_client.post(
            "/api/support/guest",
            {
                "subject": "Не можу увійти",
                "request_type": "account",
                "text": "Допоможіть, будь ласка.",
                "desired_outcome": "Відновити доступ до акаунта.",
                "contact_email": "guest@example.com",
                "contact_phone": "+380501112233",
                "contact_telegram": "@guest",
                "contact_instagram": "@guest.insta",
                "contact_viber": "+380501112244",
                "contact_whatsapp": "+380501112255",
                "contact_facebook": "https://facebook.com/guest",
                "contact_info": "@guest_telegram",
                "target_username": account_user.username,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, api_data(response))
        ticket = SupportTicket.objects.get()
        self.assertIsNone(ticket.user_id)
        self.assertEqual(ticket.contact_email, "guest@example.com")
        self.assertEqual(ticket.contact_phone, "+380501112233")
        self.assertEqual(ticket.contact_telegram, "@guest")
        self.assertEqual(ticket.contact_instagram, "@guest.insta")
        self.assertEqual(ticket.contact_viber, "+380501112244")
        self.assertEqual(ticket.contact_whatsapp, "+380501112255")
        self.assertEqual(ticket.contact_facebook, "https://facebook.com/guest")
        self.assertEqual(ticket.contact_info, "@guest_telegram")
        contacts = api_data(response)["contacts"]
        self.assertEqual(
            [(contact.get("label", ""), contact["value"]) for contact in contacts],
            [
                ("", "guest@example.com"),
                ("", "+380501112233"),
                ("Telegram", "@guest"),
                ("Viber", "+380501112244"),
                ("WhatsApp", "+380501112255"),
                ("Instagram", "@guest.insta"),
                ("Facebook", "https://facebook.com/guest"),
                ("Інші", "@guest_telegram"),
            ],
        )
        self.assertEqual(contacts[2]["url"], "https://t.me/guest")
        self.assertEqual(contacts[5]["url"], "https://instagram.com/guest.insta")
        self.assertEqual(ticket.request_type, "account")
        self.assertEqual(ticket.target_username, account_user.username)
        self.assertEqual(ticket.desired_outcome, "Відновити доступ до акаунта.")
        self.assertEqual(ticket.messages.count(), 1)

    def test_account_ticket_rejects_unknown_username(self):
        response = self.api_client.post(
            "/api/support/guest",
            {
                "subject": "Не можу увійти",
                "request_type": "account",
                "text": "Допоможіть, будь ласка.",
                "desired_outcome": "Відновити доступ до акаунта.",
                "contact_email": "guest@example.com",
                "target_username": "missing_account",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("target_username", api_data(response).get("fields", {}))
        self.assertFalse(SupportTicket.objects.exists())

    def test_guest_requires_email(self):
        response = self.api_client.post(
            "/api/support/guest",
            {
                "subject": "Питання",
                "request_type": "other",
                "text": "Текст",
                "desired_outcome": "Отримати відповідь.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("contact_email", api_data(response).get("fields", {}))

    def test_banned_user_can_write_to_support(self):
        user = make_user("banned1")
        Ban.objects.create(user=user, reason="тест")
        self.api_client.force_authenticate(user=user)
        response = self.api_client.post(
            "/api/support/guest",
            {
                "subject": "Питання",
                "request_type": "unban",
                "text": "Поясніть блокування.",
                "desired_outcome": "Переглянути блокування акаунта.",
                "contact_email": user.email,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, api_data(response))
        ticket = SupportTicket.objects.get()
        self.assertEqual(ticket.user_id, user.id)
        self.assertTrue(ticket.from_banned)

    def test_guest_support_is_rate_limited(self):
        payload = {
            "subject": "Питання",
            "request_type": "other",
            "text": "Текст",
            "desired_outcome": "Отримати відповідь.",
            "contact_email": "guest@example.com",
        }
        for _ in range(5):
            response = self.api_client.post("/api/support/guest", payload, format="json")
            self.assertEqual(response.status_code, 201, api_data(response))

        limited = self.api_client.post("/api/support/guest", payload, format="json")

        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["error"], "Забагато запитів. Спробуйте пізніше.")


class SupportUserTests(SupportAPITestCase):
    def setUp(self):
        cache.clear()
        self.api_client = APIClient()
        self.user = make_user("alice")
        self.other = make_user("bob")

    def test_user_creates_lists_and_replies(self):
        self.api_client.force_authenticate(user=self.user)
        created = self.api_client.post(
            "/api/support/tickets",
            {
                "subject": "Питання",
                "request_type": "bug",
                "text": "Перше повідомлення",
                "desired_outcome": "Виправити помилку.",
                "contact_phone": "+380501234567",
                "contact_telegram": "@alice",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, api_data(created))
        self.assertEqual(api_data(created)["request_type"], "bug")
        self.assertEqual(api_data(created)["desired_outcome"], "Виправити помилку.")
        self.assertEqual(api_data(created)["contacts"][0]["value"], self.user.email)
        self.assertEqual(api_data(created)["contacts"][1]["value"], "+380501234567")
        ticket_id = api_data(created)["id"]

        listing = self.api_client.get("/api/support/tickets")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(api_data(listing)["count"], 1)
        self.assertEqual(
            api_data(listing)["results"][0]["contacts"][2],
            {"value": "@alice", "label": "Telegram", "url": "https://t.me/alice"},
        )

        reply = self.api_client.post(
            f"/api/support/tickets/{ticket_id}/messages",
            {"text": "Додаю деталі"},
            format="json",
        )
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(len(api_data(reply)["messages"]), 2)

    def test_user_cannot_open_foreign_ticket(self):
        ticket = SupportTicket.objects.create(user=self.other, subject="Чуже")
        SupportMessage.objects.create(ticket=ticket, sender=self.other, text="текст")
        self.api_client.force_authenticate(user=self.user)
        response = self.api_client.get(f"/api/support/tickets/{ticket.id}")
        self.assertEqual(response.status_code, 404)

    def test_ban_notice_request_attaches_only_own_active_ban(self):
        ban = Ban.objects.create(user=self.user, reason="Причина з адмінки")
        self.api_client.force_authenticate(user=self.user)
        created = self.api_client.post(
            "/api/support/tickets",
            {
                "subject": "Оскарження блокування",
                "request_type": "unban",
                "text": "Прошу переглянути бан.",
                "desired_outcome": "Перевірити причину блокування.",
                "appeal_ban_id": str(ban.pk),
            },
            format="json",
        )

        self.assertEqual(created.status_code, 201, api_data(created))
        ticket = SupportTicket.objects.get(pk=api_data(created)["id"])
        self.assertEqual(ticket.appeal_ban_id, ban.pk)
        self.assertTrue(
            SupportAction.objects.filter(
                ticket=ticket,
                action="appeal_submitted",
                metadata={"ban_id": ban.pk},
            ).exists()
        )

    def test_ban_notice_request_cannot_attach_another_users_ban(self):
        other_ban = Ban.objects.create(user=self.other, reason="Чужий бан")
        self.api_client.force_authenticate(user=self.user)

        response = self.api_client.post(
            "/api/support/tickets",
            {
                "subject": "Оскарження блокування",
                "request_type": "unban",
                "text": "Прошу переглянути бан.",
                "desired_outcome": "Перевірити причину блокування.",
                "appeal_ban_id": str(other_ban.pk),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(SupportTicket.objects.exists())

    def test_closed_ticket_cannot_be_replied(self):
        self.api_client.force_authenticate(user=self.user)
        created = self.api_client.post(
            "/api/support/tickets",
            {
                "subject": "S",
                "request_type": "other",
                "text": "T",
                "desired_outcome": "Отримати відповідь.",
            },
            format="json",
        )
        ticket = SupportTicket.objects.get(pk=api_data(created)["id"])
        ticket.status = "closed"
        ticket.save(update_fields=["status"])
        response = self.api_client.post(
            f"/api/support/tickets/{ticket.id}/messages", {"text": "ще"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_ticket_list_query_count_does_not_grow_per_ticket(self):
        self.api_client.force_authenticate(user=self.user)
        first = SupportTicket.objects.create(user=self.user, subject="Перше")
        SupportMessage.objects.create(ticket=first, sender=self.user, text="Перший текст")
        with CaptureQueriesContext(connection) as one_ticket_queries:
            response = self.api_client.get("/api/support/tickets")
        self.assertEqual(response.status_code, 200)

        for number in range(2, 4):
            ticket = SupportTicket.objects.create(user=self.user, subject=f"Звернення {number}")
            SupportMessage.objects.create(
                ticket=ticket, sender=self.user, text=f"Текст {number}"
            )
        with CaptureQueriesContext(connection) as three_ticket_queries:
            response = self.api_client.get("/api/support/tickets")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(one_ticket_queries), len(three_ticket_queries))


class SupportAdminTests(SupportAPITestCase):
    def setUp(self):
        cache.clear()
        self.api_client = APIClient()
        self.user = make_user("carol")
        self.admin = make_user("sup_admin", role=CustomUser.ROLE_ADMIN_2)
        AdminRight.objects.create(user=self.admin, right="support")
        self.no_support_admin = make_user("other_admin", role=CustomUser.ROLE_ADMIN_2)
        AdminRight.objects.create(user=self.no_support_admin, right="home")
        self.main = make_user("main", role=CustomUser.ROLE_ADMIN_MAIN)
        self.ticket = SupportTicket.objects.create(user=self.user, subject="Питання")
        SupportMessage.objects.create(ticket=self.ticket, sender=self.user, text="Допоможіть")

    def test_accessible_only_with_support_right(self):
        self.api_client.force_authenticate(user=self.user)
        self.assertEqual(self.api_client.get("/api/admin/support").status_code, 403)

        self.api_client.force_authenticate(user=self.no_support_admin)
        self.assertEqual(self.api_client.get("/api/admin/support").status_code, 403)

        self.api_client.force_authenticate(user=self.admin)
        self.assertEqual(self.api_client.get("/api/admin/support").status_code, 200)

        self.api_client.force_authenticate(user=self.main)
        self.assertEqual(self.api_client.get("/api/admin/support").status_code, 200)

    def test_admin_can_filter_tickets_by_request_type(self):
        bug_ticket = SupportTicket.objects.create(
            user=self.user,
            subject="Помилка",
            request_type="bug",
            desired_outcome="Виправити помилку.",
        )
        self.api_client.force_authenticate(user=self.admin)

        response = self.api_client.get("/api/admin/support?request_type=bug")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(api_data(response)["count"], 1)
        self.assertEqual(api_data(response)["results"][0]["id"], bug_ticket.id)
        self.assertEqual(api_data(response)["results"][0]["request_type_display"], "Виправлення помилки")

    def test_support_level_two_can_review_ban_request_with_attached_reason(self):
        ban = Ban.objects.create(
            user=self.user,
            reason="Причина з повідомлення адміністратору",
            issued_by=self.admin,
        )
        ticket = SupportTicket.objects.create(
            user=self.user,
            subject="Оскарження блокування",
            request_type="unban",
            appeal_ban=ban,
        )
        SupportMessage.objects.create(
            ticket=ticket,
            sender=self.user,
            text="Причина блокування: Причина з повідомлення адміністратору",
        )
        self.api_client.force_authenticate(user=self.admin)

        listing = self.api_client.get("/api/admin/support?request_type=unban")
        detail = self.api_client.get(f"/api/admin/support/{ticket.pk}")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(
            [row["id"] for row in api_data(listing)["results"]],
            [ticket.pk],
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            api_data(detail)["appeal_ban"]["reason"],
            "Причина з повідомлення адміністратору",
        )

    def test_admin_ticket_list_formats_new_and_legacy_contacts(self):
        ticket = SupportTicket.objects.create(
            user=None,
            subject="Гостьове звернення",
            contact_email="guest@example.com",
            contact_phone="+380501112233",
            contact_viber="+380501112244",
            contact_info="старий контакт",
        )
        self.api_client.force_authenticate(user=self.admin)

        response = self.api_client.get("/api/admin/support")

        self.assertEqual(response.status_code, 200)
        row = next(item for item in api_data(response)["results"] if item["id"] == ticket.id)
        self.assertEqual(
            row["contacts"],
            [
                {"value": "guest@example.com"},
                {"value": "+380501112233"},
                {"value": "+380501112244", "label": "Viber"},
                {"value": "старий контакт", "label": "Інші"},
            ],
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_admin_reply_to_guest_is_emailed(self):
        guest_ticket = SupportTicket.objects.create(
            user=None,
            subject="Не можу увійти",
            request_type="account",
            contact_email="guest@example.com",
            target_username=self.user.username,
        )
        self.api_client.force_authenticate(user=self.admin)

        response = self.api_client.post(
            f"/api/admin/support/{guest_ticket.id}",
            {"text": "Перевірте пошту та спробуйте відновити пароль."},
            format="json",
        )

        self.assertEqual(response.status_code, 201, api_data(response))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["guest@example.com"])
        self.assertIn("Перевірте пошту", mail.outbox[0].body)
        self.assertEqual(api_data(response)["target_username"], self.user.username)
        self.assertEqual(guest_ticket.messages.filter(from_side="admin").count(), 1)

    def test_ticket_requires_valid_type_and_desired_outcome(self):
        self.api_client.force_authenticate(user=self.user)

        response = self.api_client.post(
            "/api/support/tickets",
            {"subject": "Питання", "request_type": "unknown", "text": "Текст"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        fields = api_data(response).get("fields", {})
        self.assertIn("request_type", fields)
        self.assertIn("desired_outcome", fields)

    def test_admin_reply_notifies_user_and_marks_answered(self):
        self.api_client.force_authenticate(user=self.admin)
        response = self.api_client.post(
            f"/api/admin/support/{self.ticket.id}",
            {"text": "Вітаю, допомагаю."},
            format="json",
        )
        self.assertEqual(response.status_code, 201, api_data(response))
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "answered")
        self.assertEqual(self.ticket.assigned_admin_id, self.admin.id)
        self.assertTrue(
            self.user.notifications.filter(section="support").exists()  # pyright: ignore[reportAttributeAccessIssue]
        )

    def test_status_change_is_logged(self):
        self.api_client.force_authenticate(user=self.admin)
        response = self.api_client.post(
            f"/api/admin/support/{self.ticket.id}/status", {"status": "closed"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "closed")
        self.assertTrue(AdminLog.objects.filter(actor=self.admin).exists())

    def test_recover_account_generates_temp_password(self):
        self.ticket.request_type = "account"
        self.ticket.save(update_fields=["request_type"])
        self.api_client.force_authenticate(user=self.admin)
        response = self.api_client.post(
            f"/api/admin/support/{self.ticket.id}/recover-account",
            {"target_login": "carol", "contacted_via": "email"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, api_data(response))
        data = api_data(response)
        self.assertTrue(data["temp_password"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.must_change_password)
        self.assertTrue(self.user.check_password(data["temp_password"]))
        self.assertTrue(AccountRecovery.objects.filter(ticket=self.ticket, target_user=self.user).exists())

    def test_recover_account_cannot_target_another_user(self):
        self.ticket.request_type = "account"
        self.ticket.save(update_fields=["request_type"])
        other_user = make_user("other_recovery_target")
        self.api_client.force_authenticate(user=self.admin)

        by_login = self.api_client.post(
            f"/api/admin/support/{self.ticket.id}/recover-account",
            {"target_login": other_user.username},
            format="json",
        )
        by_id = self.api_client.post(
            f"/api/admin/support/{self.ticket.id}/recover-account",
            {"target_user_id": other_user.id},
            format="json",
        )

        self.assertEqual(by_login.status_code, 403)
        self.assertEqual(by_id.status_code, 403)
        other_user.refresh_from_db()
        self.assertTrue(other_user.check_password("pass12345"))
        self.assertFalse(AccountRecovery.objects.filter(target_user=other_user).exists())

    def test_admin2_cannot_recover_admin_account(self):
        admin1 = make_user("recovery_admin1", role=CustomUser.ROLE_ADMIN_1)
        ticket = SupportTicket.objects.create(
            user=admin1, subject="Проблема з акаунтом", request_type="account"
        )
        self.api_client.force_authenticate(user=self.admin)

        response = self.api_client.post(
            f"/api/admin/support/{ticket.id}/recover-account",
            {"target_user_id": admin1.id},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        admin1.refresh_from_db()
        self.assertTrue(admin1.check_password("pass12345"))
        self.assertFalse(AccountRecovery.objects.filter(target_user=admin1).exists())

    def test_admin2_can_lift_system_or_admin2_ban_but_not_admin1_ban(self):
        AdminRight.objects.create(user=self.admin, right="bans")
        ban = Ban.objects.create(
            user=self.user, issued_by=self.admin, ends_at=timezone.now() + timedelta(days=5)
        )
        self.assertEqual(ban.issued_by_role, CustomUser.ROLE_ADMIN_2)
        unban_ticket = SupportTicket.objects.create(
            user=self.user, subject="Розбан", request_type="unban"
        )
        self.api_client.force_authenticate(user=self.admin)
        response = self.api_client.post(
            f"/api/admin/support/{unban_ticket.id}/ban/lift",
            {"reason": "Перевірено"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, api_data(response))
        ban.refresh_from_db()
        self.assertFalse(ban.is_active)

        admin1 = make_user("ban_admin1", role=CustomUser.ROLE_ADMIN_1)
        higher_ban = Ban.objects.create(
            user=self.user, issued_by=admin1, ends_at=timezone.now() + timedelta(days=5)
        )
        response = self.api_client.post(
            f"/api/admin/support/{unban_ticket.id}/ban/lift",
            {"reason": "Повторна спроба"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        higher_ban.refresh_from_db()
        self.assertTrue(higher_ban.is_active)

    def test_unban_rejection_creates_single_appeal_only_visible_to_admin1(self):
        ban = Ban.objects.create(user=self.user, issued_by=self.main, reason="Перевірка")
        ticket = SupportTicket.objects.create(
            user=self.user, subject="Прошу розбан", request_type="unban"
        )
        self.api_client.force_authenticate(user=self.main)
        rejected = self.api_client.post(
            f"/api/admin/support/{ticket.id}/ban/reject",
            {"reason": "Причина блокування підтвердилася."},
            format="json",
        )
        self.assertEqual(rejected.status_code, 201, api_data(rejected))
        rejection = SupportAction.objects.get(ticket=ticket, action="unban_rejected")
        self.assertIn("Причина блокування", api_data(rejected)["messages"][-1]["text"])
        self.assertEqual(rejection.metadata["ban_id"], ban.id)

        self.api_client.force_authenticate(user=self.user)
        detail = self.api_client.get(f"/api/support/tickets/{ticket.id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(api_data(detail)["appeal_rejections"], [{"id": rejection.id}])
        appeal_response = self.api_client.post(
            f"/api/support/tickets/{ticket.id}/appeals",
            {"rejection_id": rejection.id, "text": "Прошу переглянути рішення."},
            format="json",
        )
        self.assertEqual(appeal_response.status_code, 201, api_data(appeal_response))
        appeal = SupportTicket.objects.get(pk=api_data(appeal_response)["id"])
        self.assertEqual(appeal.request_type, "appeal")
        self.assertEqual(appeal.parent_ticket_id, ticket.id)
        self.assertEqual(appeal.appeal_ban_id, ban.id)

        duplicate = self.api_client.post(
            f"/api/support/tickets/{ticket.id}/appeals",
            {"rejection_id": rejection.id, "text": "Дубль."},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400)
        self.api_client.force_authenticate(user=self.admin)
        self.assertNotIn(
            appeal.id,
            [row["id"] for row in api_data(self.api_client.get("/api/admin/support"))["results"]],
        )
        self.assertEqual(self.api_client.get(f"/api/admin/support/{appeal.id}").status_code, 404)
        self.api_client.force_authenticate(user=self.main)
        self.assertEqual(self.api_client.get(f"/api/admin/support/{appeal.id}").status_code, 200)

    def test_bug_status_and_internal_note_are_admin_only(self):
        ticket = SupportTicket.objects.create(
            user=self.user, subject="Баг", request_type="bug"
        )
        self.api_client.force_authenticate(user=self.admin)
        response = self.api_client.post(
            f"/api/admin/support/{ticket.id}/bug/status",
            {"status": "in_progress", "internal_note": "Перевірити журнал помилок."},
            format="json",
        )
        self.assertEqual(response.status_code, 200, api_data(response))
        self.assertEqual(api_data(response)["bug_status"], "in_progress")
        self.assertEqual(api_data(response)["internal_note"], "Перевірити журнал помилок.")
        self.assertTrue(SupportAction.objects.filter(ticket=ticket, action="bug_status").exists())

        self.api_client.force_authenticate(user=self.user)
        detail = self.api_client.get(f"/api/support/tickets/{ticket.id}")
        self.assertNotIn("internal_note", api_data(detail))
        self.assertNotIn("actions", api_data(detail))

    def test_wallet_issue_requires_wallet_category_and_records_reason(self):
        ticket = SupportTicket.objects.create(
            user=self.user, subject="Баланс", request_type="payment"
        )
        self.api_client.force_authenticate(user=self.admin)
        denied = self.api_client.post(
            f"/api/admin/support/{ticket.id}/wallet/issue",
            {"amount_mc": "2.123456", "reason": "Компенсація"},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

        self.api_client.force_authenticate(user=self.main)
        response = self.api_client.post(
            f"/api/admin/support/{ticket.id}/wallet/issue",
            {"amount_mc": "2.123456", "reason": "Компенсація за збій."},
            format="json",
        )
        self.assertEqual(response.status_code, 201, api_data(response))
        self.assertEqual(api_data(response)["amount_mc"], "2.123456")
        self.assertTrue(CoinIssue.objects.filter(to_user=self.user, issued_by=self.main).exists())
        self.assertTrue(SupportAction.objects.filter(ticket=ticket, action="wallet_issue").exists())

    def test_wallet_history_supports_pagination_and_order_filter(self):
        ticket = SupportTicket.objects.create(
            user=self.user, subject="Історія гаманця", request_type="payment"
        )
        seller = make_user("wallet_history_seller")
        order = Order.objects.create(
            buyer=self.user, seller=seller, total_mc=Decimal("10.000000")
        )
        wallet, _ = Wallet.objects.get_or_create(user=self.user)
        WalletTransaction.objects.bulk_create(
            [
                WalletTransaction(
                    wallet=wallet,
                    amount_mc=Decimal("1.000000"),
                    kind="escrow_hold",
                    order=order,
                    note=f"Операція {index}",
                )
                for index in range(12)
            ]
        )
        self.api_client.force_authenticate(user=self.main)

        first_page = self.api_client.get(f"/api/admin/support/{ticket.id}/wallet")
        self.assertEqual(first_page.status_code, 200, api_data(first_page))
        self.assertEqual(len(api_data(first_page)["transactions"]), 10)
        self.assertTrue(api_data(first_page)["transactions_has_more"])

        next_page = self.api_client.get(
            f"/api/admin/support/{ticket.id}/wallet?offset=10"
        )
        self.assertEqual(next_page.status_code, 200, api_data(next_page))
        self.assertEqual(len(api_data(next_page)["transactions"]), 2)
        self.assertFalse(api_data(next_page)["transactions_has_more"])

        filtered = self.api_client.get(
            f"/api/admin/support/{ticket.id}/wallet?order_id={order.id}"
        )
        self.assertEqual(filtered.status_code, 200, api_data(filtered))
        self.assertEqual(len(api_data(filtered)["transactions"]), 10)
        self.assertTrue(api_data(filtered)["transactions_has_more"])

    def test_order_refund_records_reason_and_updates_escrow(self):
        ticket = SupportTicket.objects.create(
            user=self.user, subject="Повернення", request_type="order"
        )
        seller = make_user("order_seller")
        order = Order.objects.create(
            buyer=self.user, seller=seller, total_mc=Decimal("10.000000")
        )
        buyer_wallet, _ = Wallet.objects.get_or_create(user=self.user)
        buyer_wallet.held_mc = Decimal("10.000000")
        buyer_wallet.save(update_fields=["held_mc"])
        hold = EscrowHold.objects.create(
            order=order,
            buyer=self.user,
            seller=seller,
            amount_mc=Decimal("10.000000"),
            fee_mc=Decimal("0.300000"),
        )
        self.api_client.force_authenticate(user=self.main)
        response = self.api_client.post(
            f"/api/admin/support/{ticket.id}/order/refund-buyer",
            {"order_id": order.id, "reason": "Замовлення не доставлено."},
            format="json",
        )
        self.assertEqual(response.status_code, 201, api_data(response))
        buyer_wallet.refresh_from_db()
        hold.refresh_from_db()
        self.assertEqual(buyer_wallet.held_mc, Decimal("0.000000"))
        self.assertEqual(buyer_wallet.free_balance_mc, Decimal("10.000000"))
        self.assertEqual(hold.status, "refunded")
        self.assertEqual(Refund.objects.get(order=order).approved_by_id, self.main.id)
        self.assertTrue(SupportAction.objects.filter(ticket=ticket, action="order_refund_buyer").exists())

    def test_cannot_recover_main_admin(self):
        other_ticket = SupportTicket.objects.create(
            user=None, subject="Гість", contact_email="g@example.com", request_type="account"
        )
        self.api_client.force_authenticate(user=self.admin)
        response = self.api_client.post(
            f"/api/admin/support/{other_ticket.id}/recover-account",
            {"target_login": "main"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

        main_ticket = SupportTicket.objects.create(
            user=self.main, subject="Проблема з акаунтом", request_type="account"
        )
        self.api_client.force_authenticate(user=self.main)
        linked_response = self.api_client.post(
            f"/api/admin/support/{main_ticket.id}/recover-account",
            {"target_user_id": self.main.id},
            format="json",
        )
        self.assertEqual(linked_response.status_code, 403)

    def test_counter_counts_open_tickets(self):
        self.api_client.force_authenticate(user=self.admin)
        counters = api_data(self.api_client.get("/api/notifications/counters"))["counters"]
        self.assertEqual(counters.get("admin_support"), 1)


class SupportAttachmentTests(SupportAPITestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.owner = make_user("owner1")
        self.other = make_user("stranger1")
        self.admin = make_user("att_admin", role=CustomUser.ROLE_ADMIN_2)
        AdminRight.objects.create(user=self.admin, right="support")
        self.no_support_admin = make_user(
            "att_other_admin", role=CustomUser.ROLE_ADMIN_2
        )
        AdminRight.objects.create(user=self.no_support_admin, right="home")
        self.main = make_user("att_main", role=CustomUser.ROLE_ADMIN_MAIN)
        self.ticket = SupportTicket.objects.create(user=self.owner, subject="Файл")
        self.message = SupportMessage.objects.create(
            ticket=self.ticket,
            sender=self.owner,
            text="див. файл",
            attachment=SimpleUploadedFile("note.txt", b"hello", content_type="text/plain"),
        )

    def test_attachment_only_for_owner_and_support_admin(self):
        self.api_client.force_authenticate(user=self.other)
        self.assertEqual(self.api_client.get(f"/api/support/attachments/{self.message.id}").status_code, 403)

        self.api_client.force_authenticate(user=self.owner)
        self.assertEqual(self.api_client.get(f"/api/support/attachments/{self.message.id}").status_code, 200)

        self.api_client.force_authenticate(user=self.admin)
        self.assertEqual(self.api_client.get(f"/api/support/attachments/{self.message.id}").status_code, 200)

        self.api_client.force_authenticate(user=self.no_support_admin)
        self.assertEqual(self.api_client.get(f"/api/support/attachments/{self.message.id}").status_code, 403)

        self.api_client.force_authenticate(user=self.main)
        self.assertEqual(self.api_client.get(f"/api/support/attachments/{self.message.id}").status_code, 200)

    def test_admin_ticket_list_query_count_does_not_grow_per_ticket(self):
        self.api_client.force_authenticate(user=self.admin)
        with CaptureQueriesContext(connection) as one_ticket_queries:
            response = self.api_client.get("/api/admin/support")
        self.assertEqual(response.status_code, 200)

        for number in range(2, 4):
            ticket = SupportTicket.objects.create(
                user=self.owner, subject=f"Звернення {number}"
            )
            SupportMessage.objects.create(
                ticket=ticket, sender=self.owner, text=f"Текст {number}"
            )
        with CaptureQueriesContext(connection) as three_ticket_queries:
            response = self.api_client.get("/api/admin/support")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(one_ticket_queries), len(three_ticket_queries))
