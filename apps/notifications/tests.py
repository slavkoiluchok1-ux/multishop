from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import AdminRight, CustomUser
from apps.complaints.models import Complaint, ComplaintType
from apps.support.models import SupportTicket

from .models import Notification


class NotificationCounterCacheTests(TestCase):
    def test_cached_counts_are_user_scoped_and_invalidated(self):
        cache.clear()
        user = CustomUser.objects.create_user(
            username="counter-user", email="counter@example.com", password="test-password-123"
        )
        other = CustomUser.objects.create_user(
            username="other-counter",
            email="other-counter@example.com",
            password="test-password-123",
        )
        Notification.objects.create(
            user=user, section="orders", kind="order", text="Нове замовлення"
        )
        client = APIClient()
        client.force_authenticate(user=user)
        other_client = APIClient()
        other_client.force_authenticate(user=other)

        self.assertEqual(
            client.get("/api/notifications/counters").json()["counters"]["orders"], 1
        )
        self.assertEqual(
            other_client.get("/api/notifications/counters").json()["counters"]["orders"], 0
        )
        client.post("/api/notifications/read", {"section": "orders"}, format="json")

        self.assertEqual(
            client.get("/api/notifications/counters").json()["counters"]["orders"], 0
        )


class AdminBadgeTests(TestCase):
    def setUp(self):
        self.reporter = self.make_user("badge-reporter")
        self.target = self.make_user("badge-target")
        self.main_admin = self.make_user("badge-main", CustomUser.ROLE_ADMIN_MAIN)
        self.support_admin = self.make_user("badge-support", CustomUser.ROLE_ADMIN_2)
        self.complaints_admin = self.make_user("badge-complaints", CustomUser.ROLE_ADMIN_2)
        AdminRight.objects.create(user=self.support_admin, right="support")
        AdminRight.objects.create(user=self.complaints_admin, right="complaints")

        complaint_type = ComplaintType.objects.create(
            name="Скарги на замовлення",
            view="orders",
            handler_rights=["complaints"],
        )
        for status in ("new", "in_review", "upheld"):
            Complaint.objects.create(
                author=self.reporter,
                target_user=self.target,
                complaint_type=complaint_type,
                target_kind="user",
                text="Опис скарги",
                status=status,
            )

        for request_type, status in (
            ("other", "open"),
            ("other", "answered"),
            ("other", "closed"),
            ("appeal", "open"),
        ):
            SupportTicket.objects.create(
                user=self.reporter,
                request_type=request_type,
                subject="Звернення",
                status=status,
            )

    @staticmethod
    def make_user(username, role=CustomUser.ROLE_USER):
        user = CustomUser.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="test-password-123",
        )
        if role != CustomUser.ROLE_USER:
            user.role = role
            user.save(update_fields=["role"])
        return user

    @staticmethod
    def counters_for(user):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client.get("/api/admin/badges")

    def test_main_admin_sees_open_complaints_and_pending_support(self):
        response = self.counters_for(self.main_admin)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"complaints": 2, "support": 2, "total": 4},
        )

    def test_level_two_sees_only_counters_for_assigned_rights(self):
        support_response = self.counters_for(self.support_admin)
        complaints_response = self.counters_for(self.complaints_admin)

        self.assertEqual(
            support_response.json(),
            {"complaints": 0, "support": 1, "total": 1},
        )
        self.assertEqual(
            complaints_response.json(),
            {"complaints": 2, "support": 0, "total": 2},
        )

    def test_regular_user_and_guest_cannot_read_admin_badges(self):
        user_response = self.counters_for(self.reporter)
        guest_response = self.counters_for(None)

        self.assertEqual(user_response.status_code, 403)
        self.assertIn(guest_response.status_code, (401, 403))
