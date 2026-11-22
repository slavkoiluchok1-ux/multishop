from io import BytesIO
from decimal import Decimal
from pathlib import Path
import tempfile
from typing import Any, Protocol, cast

from django.contrib import admin as django_admin
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from PIL import Image
from rest_framework.test import APIClient

from apps.core.admin import ReadOnlyAdminMixin
from apps.notifications.models import Notification
from apps.wallet.models import CoinIssue, Wallet, WalletTransaction

from .models import AdminLog, AdminRight, Ban, CustomUser, SellerVerification


class APIClientResponse(Protocol):
    status_code: int
    data: Any

    def json(self) -> Any: ...


def api_post(client: APIClient, *args: Any, **kwargs: Any) -> APIClientResponse:
    return cast(APIClientResponse, client.post(*args, **kwargs))


class AuthenticationSessionTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="session-user",
            email="session-user@example.com",
            password="test-password-123",
        )

    def test_login_by_nickname_or_email_sets_a_working_session_and_logout_clears_it(self):
        for login_value in ("session-user", "SESSION-USER@EXAMPLE.COM"):
            with self.subTest(login=login_value):
                client = APIClient(enforce_csrf_checks=True)
                client.get("/api/auth/csrf")
                csrf_token = client.cookies["csrftoken"].value

                response = client.post(
                    "/api/auth/login",
                    {"login": login_value, "password": "test-password-123"},
                    format="json",
                    HTTP_X_CSRFTOKEN=csrf_token,
                )

                self.assertEqual(response.status_code, 200, response.data)
                self.assertIn("sessionid", response.cookies)
                self.assertFalse(response.cookies["sessionid"]["secure"])
                current_user = client.get("/api/auth/me")
                self.assertTrue(current_user.json()["authenticated"])
                self.assertEqual(current_user.json()["username"], self.user.username)
                csrf_token = client.cookies["csrftoken"].value

                logout = client.post(
                    "/api/auth/logout",
                    {},
                    format="json",
                    HTTP_X_CSRFTOKEN=csrf_token,
                )

                self.assertEqual(logout.status_code, 200, logout.data)
                self.assertFalse(client.get("/api/auth/me").json()["authenticated"])


class ProfileUpdateApiTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="profile-user",
            email="profile-user@example.com",
            password="profile-password-123",
        )
        self.other_user = CustomUser.objects.create_user(
            username="other-profile-user",
            email="other-profile-user@example.com",
            password="other-password-123",
        )
        self.api_client = APIClient()
        self.api_client.force_authenticate(self.user)

    def test_patch_persists_every_field_and_login_reads_saved_values(self):
        payload = {
            "display_name": "Публічний Олена",
            "email": "profile-user-updated@example.com",
            "phone": "+380501234567",
            "telegram": "@multishopuser",
            "instagram": "@multishop",
            "viber": "+380671234567",
            "facebook": "https://www.facebook.com/multishop",
            "whatsapp": "+380931234567",
            "recipient_name": "Олена Покупець",
            "delivery_country": "Україна",
            "delivery_city": "Київ",
            "delivery_street": "вул. Хрещатик, 1",
            "delivery_postcode": "01001",
            "autofill_delivery": False,
        }

        response = self.api_client.patch("/api/profile", payload, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        profile_fields = (
            "phone",
            "telegram",
            "instagram",
            "viber",
            "facebook",
            "whatsapp",
            "recipient_name",
            "delivery_country",
            "delivery_city",
            "delivery_street",
            "delivery_postcode",
            "autofill_delivery",
        )
        self.user.refresh_from_db()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.username, "profile-user")
        self.assertEqual(self.user.display_name, payload["display_name"])
        self.assertEqual(self.user.email, payload["email"])
        for field in profile_fields:
            with self.subTest(field=field):
                self.assertEqual(getattr(self.user.profile, field), payload[field])
                self.assertEqual(response.data["profile"][field], payload[field])

        self.api_client.force_authenticate(user=None)
        login = self.api_client.post(
            "/api/auth/login",
            {"login": "profile-user", "password": "profile-password-123"},
            format="json",
        )
        self.assertEqual(login.status_code, 200, login.data)
        reloaded = self.api_client.get("/api/profile")
        self.assertEqual(reloaded.status_code, 200, reloaded.data)
        for field in profile_fields:
            with self.subTest(reloaded_field=field):
                self.assertEqual(reloaded.data["profile"][field], payload[field])

    def test_profile_update_only_changes_the_authenticated_users_profile(self):
        response = self.api_client.patch(
            "/api/profile",
            {"user_id": self.other_user.pk, "phone": "+380501234567"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.user.profile.refresh_from_db()
        self.other_user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.phone, "+380501234567")
        self.assertEqual(self.other_user.profile.phone, "")

    def test_patch_preserves_fields_not_in_the_request(self):
        self.user.profile.delivery_city = "Одеса"
        self.user.profile.save()

        response = self.api_client.patch(
            "/api/profile",
            {"phone": "+380501234567"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.phone, "+380501234567")
        self.assertEqual(self.user.profile.delivery_city, "Одеса")

    def test_invalid_contact_fields_are_reported(self):
        response = self.api_client.patch(
            "/api/profile",
            {
                "phone": "0501234567",
                "telegram": "multishopuser",
                "facebook": "https://example.com/user",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            set(response.data["fields"]),
            {"phone", "telegram", "facebook"},
        )
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.phone, "")


def png_upload(name="verification.png"):
    content = BytesIO()
    Image.new("RGB", (2, 2), color="white").save(content, format="PNG")
    return SimpleUploadedFile(name, content.getvalue(), content_type="image/png")


class SellerVerificationApiTests(TestCase):
    user: CustomUser
    api_client: APIClient

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="seller", email="seller@example.com", password="test-password"
        )
        self.api_client = APIClient()
        self.api_client.force_authenticate(self.user)

    def tearDown(self):
        verification = SellerVerification.objects.filter(user=self.user).first()
        if verification and verification.photo.name:
            verification.photo.storage.delete(verification.photo.name)

    def test_me_contains_verification_status(self):
        SellerVerification.objects.create(
            user=self.user,
            age=25,
            photo="verification/seller.png",
            is_locked=True,
        )

        response = self.api_client.get("/api/auth/me")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["seller_verified"])
        self.assertEqual(response.data["verified_age"], 25)
        self.assertEqual(response.data["seller_verification_age"], 25)

    def test_underage_verification_is_rejected(self):
        response = api_post(
            self.api_client,
            "/api/profile/seller-verification",
            {"age": 17, "photo": png_upload()},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(SellerVerification.objects.filter(user=self.user).exists())

    def test_verification_is_validated_and_cannot_be_resubmitted(self):
        response = api_post(
            self.api_client,
            "/api/profile/seller-verification",
            {"age": 18, "photo": png_upload()},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201, response.data)
        verification = SellerVerification.objects.get(user=self.user)
        self.assertTrue(verification.is_locked)
        self.assertEqual(verification.age, 18)

        second_response = api_post(
            self.api_client,
            "/api/profile/seller-verification",
            {"age": 30, "photo": png_upload("another.png")},
            format="multipart",
        )

        self.assertEqual(second_response.status_code, 400)
        verification.refresh_from_db()
        self.assertEqual(verification.age, 18)


class ProfileAvatarApiTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        media_root = Path(self.media_dir.name)
        media_settings = override_settings(
            MEDIA_ROOT=media_root,
            PUBLIC_MEDIA_ROOT=media_root,
        )
        media_settings.enable()
        self.addCleanup(media_settings.disable)

        self.user = CustomUser.objects.create_user(
            username="avatar-user", email="avatar@example.com", password="test-password"
        )
        self.other_user = CustomUser.objects.create_user(
            username="other-user", email="other@example.com", password="test-password"
        )
        self.api_client = APIClient()
        self.api_client.force_authenticate(self.user)

    def upload(self, file):
        return self.api_client.put("/api/profile", {"avatar": file}, format="multipart")

    def avatar_errors(self, response):
        return response.data.get("fields", response.data).get("avatar", [])

    def test_valid_avatar_is_saved_with_server_generated_name(self):
        response = self.upload(png_upload("user-controlled-name.jpg"))

        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        avatar_name = self.user.avatar.name
        if avatar_name is None:
            self.fail("Uploaded avatar should have a storage name.")
        self.assertRegex(avatar_name, r"^avatars/[0-9a-f]{32}\.png$")
        self.assertTrue(Path(self.user.avatar.path).is_file())
        self.assertIn("/media/avatars/", response.data["avatar"])

    def test_avatar_larger_than_five_megabytes_is_rejected(self):
        response = self.upload(
            SimpleUploadedFile("large.png", b"x" * (5 * 1024 * 1024 + 1), content_type="image/png")
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("5 МБ", str(self.avatar_errors(response)))

    def test_text_disguised_as_jpg_is_rejected(self):
        response = self.upload(
            SimpleUploadedFile("not-an-image.jpg", b"plain text", content_type="image/jpeg")
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Файл не є коректним зображенням.", str(self.avatar_errors(response)))

    def test_svg_is_rejected(self):
        response = self.upload(
            SimpleUploadedFile(
                "avatar.svg",
                b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
                content_type="image/svg+xml",
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Файл не є коректним зображенням.", str(self.avatar_errors(response)))

    def test_gif_is_rejected_by_detected_format(self):
        content = BytesIO()
        Image.new("RGB", (2, 2), color="white").save(content, format="GIF")

        response = self.upload(
            SimpleUploadedFile("avatar.gif", content.getvalue(), content_type="image/gif")
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Дозволені формати: JPG, PNG, WebP.", str(self.avatar_errors(response)))

    def test_image_over_dimension_limit_is_rejected(self):
        content = BytesIO()
        Image.new("RGB", (4001, 1), color="white").save(content, format="PNG")

        response = self.upload(
            SimpleUploadedFile("too-wide.png", content.getvalue(), content_type="image/png")
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("4000", str(self.avatar_errors(response)))

    def test_replacing_avatar_deletes_previous_file(self):
        first_response = self.upload(png_upload("first.png"))
        self.assertEqual(first_response.status_code, 200, first_response.data)
        self.user.refresh_from_db()
        old_path = Path(self.user.avatar.path)

        second_response = self.upload(png_upload("second.png"))

        self.assertEqual(second_response.status_code, 200, second_response.data)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.avatar.path, str(old_path))
        self.assertFalse(old_path.exists())
        self.assertTrue(Path(self.user.avatar.path).is_file())

    def test_replacing_avatar_does_not_delete_files_outside_avatar_folder(self):
        legacy_path = Path(self.media_dir.name) / "legacy" / "old-avatar.png"
        legacy_path.parent.mkdir()
        legacy_path.write_bytes(b"legacy avatar")
        CustomUser.objects.filter(pk=self.user.pk).update(avatar="legacy/old-avatar.png")
        self.user.refresh_from_db()

        response = self.upload(png_upload("new.png"))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(legacy_path.is_file())

    def test_put_without_avatar_preserves_existing_file(self):
        response = self.upload(png_upload("first.png"))
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        avatar_name = self.user.avatar.name

        response = self.api_client.put(
            "/api/profile",
            {"display_name": "Avatar Public"},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar.name, avatar_name)
        self.assertTrue(Path(self.user.avatar.path).is_file())

    def test_delete_removes_avatar_and_is_idempotent(self):
        response = self.upload(png_upload("first.png"))
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        old_path = Path(self.user.avatar.path)

        response = self.api_client.delete("/api/profile/avatar")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["avatar"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.avatar.name)
        self.assertFalse(old_path.exists())

        response = self.api_client.delete("/api/profile/avatar")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["avatar"])

    def test_avatar_operations_only_change_the_authenticated_user(self):
        response = self.api_client.put(
            "/api/profile",
            {"user_id": self.other_user.pk, "avatar": png_upload("mine.png")},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.other_user.refresh_from_db()
        self.assertFalse(self.other_user.avatar.name)
        self.assertTrue(CustomUser.objects.get(pk=self.user.pk).avatar.name)

    def test_avatar_delete_requires_authentication_and_csrf(self):
        anonymous_response = APIClient().delete("/api/profile/avatar")
        self.assertIn(anonymous_response.status_code, (401, 403))

        csrf_client = APIClient(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        rejected = csrf_client.delete("/api/profile/avatar")
        self.assertEqual(rejected.status_code, 403)

        csrf_client.get("/api/auth/csrf")
        csrf_token = csrf_client.cookies["csrftoken"].value
        accepted = csrf_client.delete(
            "/api/profile/avatar",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)


class AdminAccessTests(TestCase):
    """Доступ до Django Admin: лише головний адмін, прапорці за роллю."""

    def test_create_superuser_becomes_main_admin(self):
        user = CustomUser.objects.create_superuser(
            username="root", email="root@example.com", password="test-password"
        )

        self.assertEqual(user.role, CustomUser.ROLE_ADMIN_MAIN)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_role_change_syncs_staff_flags_both_ways(self):
        user = CustomUser.objects.create_user(
            username="worker", email="worker@example.com", password="test-password"
        )
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

        user.role = CustomUser.ROLE_ADMIN_MAIN
        user.save(update_fields=["role"])
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

        user.role = CustomUser.ROLE_USER
        user.save(update_fields=["role"])
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_lower_admins_cannot_open_django_admin(self):
        for role in (CustomUser.ROLE_ADMIN_1, CustomUser.ROLE_ADMIN_2):
            with self.subTest(role=role):
                user = CustomUser.objects.create_user(
                    username=role, email=f"{role}@example.com", password="test-password"
                )
                user.role = role
                user.save(update_fields=["role"])
                self.assertFalse(user.is_staff)

                self.client.force_login(user)
                response = self.client.get("/admin/")

                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response["Location"])
                self.client.logout()

    def test_main_admin_opens_django_admin(self):
        user = CustomUser.objects.create_superuser(
            username="root", email="root@example.com", password="test-password"
        )
        self.client.force_login(user)

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 200)

    def test_wallet_is_read_only_in_django_admin(self):
        request = RequestFactory().get("/admin/")
        model_admin = django_admin.site._registry[Wallet]

        self.assertIsInstance(model_admin, ReadOnlyAdminMixin)
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))


class BanAndAdminApiTests(TestCase):
    """Розбан, застава при безстроковому бані, права лише admin_2, ліміти рівнів."""

    def setUp(self):
        self.main = CustomUser.objects.create_superuser(
            username="main", email="main@example.com", password="test-password"
        )
        self.admin1 = CustomUser.objects.create_user(
            username="a1", email="a1@example.com", password="test-password"
        )
        self.admin1.role = CustomUser.ROLE_ADMIN_1
        self.admin1.save(update_fields=["role"])
        self.admin2 = CustomUser.objects.create_user(
            username="a2", email="a2@example.com", password="test-password"
        )
        self.admin2.role = CustomUser.ROLE_ADMIN_2
        self.admin2.save(update_fields=["role"])
        AdminRight.objects.create(user=self.admin2, right="bans")
        self.user = CustomUser.objects.create_user(
            username="user", email="user@example.com", password="test-password"
        )

    def logged(self, user) -> APIClient:
        """Клієнт із сесійним входом (щоб працював middleware бану)."""
        client = APIClient()
        client.force_login(user)
        return client

    def test_ban_lift_restores_access_and_allows_banning_again(self):
        admin = self.logged(self.admin1)
        response = api_post(
            admin,
            "/api/admin/bans",
            {"login": "user", "duration": "7d", "reason": "Перевірка бану"},
        )
        self.assertEqual(response.status_code, 201)

        banned = self.logged(self.user)
        self.assertEqual(banned.get("/api/profile").status_code, 403)
        self.assertTrue(CustomUser.objects.get(pk=self.user.pk).is_banned())

        ban = Ban.objects.get(user=self.user)
        lift = api_post(admin, f"/api/admin/bans/{ban.pk}/lift", {})
        self.assertEqual(lift.status_code, 200)
        self.assertFalse(CustomUser.objects.get(pk=self.user.pk).is_banned())
        self.assertEqual(banned.get("/api/profile").status_code, 200)

        again = api_post(
            admin,
            "/api/admin/bans",
            {"login": "user", "duration": "1d", "reason": "Повторна перевірка"},
        )
        self.assertEqual(again.status_code, 201)

    def test_banned_admin2_gets_403_on_admin_api(self):
        Ban.objects.create(user=self.admin2, issued_by=self.main, source="admin", ends_at=None)
        response = self.logged(self.admin2).get("/api/admin/cabinet")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "banned")

    def test_current_user_payload_includes_active_ban_reason(self):
        ban = Ban.objects.create(
            user=self.user,
            issued_by=self.admin1,
            reason="Причина для звернення",
            source="admin",
            ends_at=None,
        )

        response = self.logged(self.user).get("/api/auth/me")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_banned"])
        self.assertEqual(response.json()["active_ban"]["id"], ban.pk)
        self.assertEqual(response.json()["active_ban"]["reason"], "Причина для звернення")

    def test_forever_ban_forfeits_deposit(self):
        wallet, _ = Wallet.objects.get_or_create(user=self.user)
        wallet.deposit_locked_mc = Decimal("50")
        wallet.save(update_fields=["deposit_locked_mc"])

        response = api_post(
            self.logged(self.admin1),
            "/api/admin/bans",
            {"login": "user", "duration": "forever", "reason": "Порушення правил"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("0"))
        self.assertTrue(
            WalletTransaction.objects.filter(wallet=wallet, kind="deposit_forfeit").exists()
        )
        self.assertTrue(AdminLog.objects.filter(action__contains="Застава").exists())

    def test_temporary_ban_keeps_deposit(self):
        wallet, _ = Wallet.objects.get_or_create(user=self.user)
        wallet.deposit_locked_mc = Decimal("50")
        wallet.save(update_fields=["deposit_locked_mc"])

        response = api_post(
            self.logged(self.admin1),
            "/api/admin/bans",
            {"login": "user", "duration": "7d", "reason": "Порушення правил"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("50"))
        self.assertFalse(
            WalletTransaction.objects.filter(wallet=wallet, kind="deposit_forfeit").exists()
        )

    def test_set_rights_on_regular_user_is_forbidden(self):
        response = api_post(
            self.logged(self.admin1),
            "/api/admin/cabinet",
            {"action": "set_rights", "login": "user", "rights": ["bans"]},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(AdminRight.objects.filter(user=self.user).exists())

    def test_regrant_level2_keeps_rights_and_grant_with_rights_saves(self):
        main = self.logged(self.main)
        first = api_post(
            main,
            "/api/admin/cabinet",
            {"action": "grant_level", "level": "2", "login": "user", "rights": ["bans", "products"]},
            format="json",
        )
        self.assertEqual(first.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, CustomUser.ROLE_ADMIN_2)
        self.assertEqual(
            set(AdminRight.objects.filter(user=self.user).values_list("right", flat=True)),
            {"bans", "products"},
        )

        second = api_post(
            main,
            "/api/admin/cabinet",
            {"action": "grant_level", "level": "2", "login": "user"},
            format="json",
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            set(AdminRight.objects.filter(user=self.user).values_list("right", flat=True)),
            {"bans", "products"},
        )

    def test_set_rights_notifies_only_added_and_removed_permissions(self):
        Notification.objects.filter(user=self.admin2).delete()
        response = api_post(
            self.logged(self.main),
            "/api/admin/cabinet",
            {"action": "set_rights", "login": "a2", "rights": ["bans", "products"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        added = Notification.objects.filter(
            user=self.admin2, kind="admin_right_added"
        ).values_list("payload", flat=True)
        self.assertEqual(
            list(added),
            [{"right": "products", "url": "/pages/admin/cabinet.html"}],
        )

        response = api_post(
            self.logged(self.main),
            "/api/admin/cabinet",
            {"action": "set_rights", "login": "a2", "rights": ["products"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Notification.objects.filter(
                user=self.admin2, kind="admin_right_removed"
            ).values_list("payload", flat=True).get(),
            {"right": "bans", "url": "/pages/admin/cabinet.html"},
        )
        self.assertEqual(
            Notification.objects.filter(user=self.admin2, kind="admin_right_added").count(),
            1,
        )

    def test_admin1_cannot_grant_level1_or_ban_admins(self):
        admin = self.logged(self.admin1)

        grant = api_post(
            admin, "/api/admin/cabinet", {"action": "grant_level", "level": "1", "login": "user"}
        )
        self.assertEqual(grant.status_code, 403)

        ban_main = api_post(
            admin,
            "/api/admin/bans",
            {"login": "main", "duration": "1d", "reason": "Причина блокування"},
        )
        self.assertEqual(ban_main.status_code, 403)

        other = CustomUser.objects.create_user(
            username="a1b", email="a1b@example.com", password="test-password"
        )
        other.role = CustomUser.ROLE_ADMIN_1
        other.save(update_fields=["role"])
        ban_other = api_post(
            admin,
            "/api/admin/bans",
            {"login": "a1b", "duration": "1d", "reason": "Причина блокування"},
        )
        self.assertEqual(ban_other.status_code, 403)

    def test_admin2_cannot_unban(self):
        ban = Ban.objects.create(user=self.user, issued_by=self.main, source="admin", ends_at=None)

        response = api_post(self.logged(self.admin2), f"/api/admin/bans/{ban.pk}/lift", {})

        self.assertEqual(response.status_code, 403)


class CoinIssueLimitTests(TestCase):
    """Видача MC не може підняти баланс понад Decimal(15,6)."""

    api_client: APIClient

    def setUp(self):
        self.main = CustomUser.objects.create_superuser(
            username="main", email="main@example.com", password="test-password"
        )
        self.user = CustomUser.objects.create_user(
            username="user", email="user@example.com", password="test-password"
        )
        self.api_client = APIClient()
        self.api_client.force_login(self.main)

    def test_second_issue_over_limit_is_rejected(self):
        first = api_post(
            self.api_client,
            "/api/admin/coins/issue",
            {"login": "user", "amount_mc": "999999999"},
            format="json",
        )
        self.assertEqual(first.status_code, 201)
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.free_balance_mc, Decimal("999999999"))

        second = api_post(
            self.api_client,
            "/api/admin/coins/issue",
            {"login": "user", "amount_mc": "1"},
            format="json",
        )

        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.json()["error"], "Баланс не може перевищувати 999 999 999 MC")
        wallet.refresh_from_db()
        self.assertEqual(wallet.free_balance_mc, Decimal("999999999"))
        self.assertEqual(CoinIssue.objects.filter(to_user=self.user).count(), 1)


class CabinetVisibilityTests(TestCase):
    """Кабінет: список адмінів і журнал обмежені рангом того, хто дивиться."""

    def _make(self, username: str, role: str) -> CustomUser:
        user = CustomUser.objects.create_user(
            username=username, email=f"{username}@example.com", password="test-password"
        )
        user.role = role
        user.save(update_fields=["role"])
        return user

    def setUp(self):
        self.main = CustomUser.objects.create_superuser(
            username="main", email="main@example.com", password="test-password"
        )
        self.admin1 = self._make("a1", CustomUser.ROLE_ADMIN_1)
        self.admin1b = self._make("a1b", CustomUser.ROLE_ADMIN_1)
        self.admin2 = self._make("a2", CustomUser.ROLE_ADMIN_2)
        self.admin2b = self._make("a2b", CustomUser.ROLE_ADMIN_2)
        self.user = CustomUser.objects.create_user(
            username="user", email="user@example.com", password="test-password"
        )
        AdminLog.objects.create(actor=self.main, action="main-did", target=self.user)
        AdminLog.objects.create(actor=self.admin1, action="a1-did", target=self.user)
        AdminLog.objects.create(actor=self.admin2, action="a2-did", target=self.user)
        AdminLog.objects.create(actor=None, action="system-did")

    def cabinet(self, user: CustomUser):
        client = APIClient()
        client.force_login(user)
        return client.get("/api/admin/cabinet")

    def test_main_sees_all_admins_and_full_log(self):
        data = self.cabinet(self.main).json()

        names = {row["username"] for row in data["admins"]}
        self.assertEqual(names, {"main", "a1", "a1b", "a2", "a2b"})

        actions = {row["action"] for row in data["log"]}
        self.assertEqual(actions, {"main-did", "a1-did", "a2-did", "system-did"})

    def test_admin1_hides_main_admin_and_their_actions(self):
        data = self.cabinet(self.admin1).json()

        names = {row["username"] for row in data["admins"]}
        self.assertEqual(names, {"a1", "a1b", "a2", "a2b"})

        # Записи головного та без актора (actor=NULL) адміну 1-го рівня не видні.
        actions = {row["action"] for row in data["log"]}
        self.assertEqual(actions, {"a1-did", "a2-did"})

    def test_admin2_sees_only_self_and_own_log(self):
        data = self.cabinet(self.admin2).json()

        names = {row["username"] for row in data["admins"]}
        self.assertEqual(names, {"a2"})

        actions = {row["action"] for row in data["log"]}
        self.assertEqual(actions, {"a2-did"})

    def test_hidden_usernames_absent_from_json(self):
        data = self.cabinet(self.admin2).json()
        present = {row["username"] for row in data["admins"]}
        present |= {row["actor"] for row in data["log"]}
        present |= {row["target"] for row in data["log"] if row["target"]}

        self.assertEqual(present, {"a2", "user"})
        for hidden in ("main", "a1", "a1b", "a2b"):
            self.assertNotIn(hidden, present)

    def test_admin_cabinet_filters_admins_and_log(self):
        client = APIClient()
        client.force_login(self.main)

        admins_response = client.get(
            "/api/admin/cabinet",
            {"admin_q": "a2b", "admin_role": "admin_2"},
        )
        self.assertEqual(admins_response.status_code, 200)
        self.assertEqual(
            [row["username"] for row in admins_response.json()["admins"]],
            ["a2b"],
        )

        log_response = client.get(
            "/api/admin/cabinet",
            {"log_q": "a2-did"},
        )
        self.assertEqual(log_response.status_code, 200)
        self.assertEqual(
            [row["action"] for row in log_response.json()["log"]],
            ["a2-did"],
        )

    def test_higher_rank_target_is_masked(self):
        AdminLog.objects.create(actor=self.admin2, action="ban-main", target=self.main)

        admin1_row = next(
            row for row in self.cabinet(self.admin1).json()["log"] if row["action"] == "ban-main"
        )
        self.assertIsNone(admin1_row["target"])

        main_row = next(
            row for row in self.cabinet(self.main).json()["log"] if row["action"] == "ban-main"
        )
        self.assertEqual(main_row["target"], "main")

    def test_admin_list_query_count_is_constant_as_admins_grow(self):
        client = APIClient()
        client.force_authenticate(self.main)

        with CaptureQueriesContext(connection) as initial:
            response = client.get("/api/admin/cabinet")
        self.assertEqual(response.status_code, 200)

        extra_admin = self._make("extra_admin2", CustomUser.ROLE_ADMIN_2)
        AdminRight.objects.create(user=extra_admin, right="support")
        with CaptureQueriesContext(connection) as expanded:
            response = client.get("/api/admin/cabinet")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(initial), len(expanded))
