"""Тести API блоків головної сторінки (право «home»)."""
import io
import os
import secrets
from typing import Iterable, cast
from unittest.mock import patch

from django.contrib.auth import password_validation
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import StreamingHttpResponse
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory

from apps.accounts.models import AdminLog, AdminRight, Ban, CustomUser
from apps.catalog.models import Attribute, Category, Product

from .exceptions import GENERIC_SERVER_ERROR, api_exception_handler
from .models import SiteBlock
from .views import page


class FrontendDebugFlagTests(TestCase):
    @override_settings(DEBUG=True)
    def test_frontend_page_exposes_debug_flag_only_in_debug_mode(self):
        request = RequestFactory().get("/pages/catalog.html")
        response = page(request, "catalog.html")

        self.assertContains(response, "<script>window.MS_DEBUG = true;</script>", html=False)

    @override_settings(DEBUG=False)
    def test_frontend_page_does_not_expose_debug_flag_outside_debug_mode(self):
        request = RequestFactory().get("/pages/catalog.html")
        response = page(request, "catalog.html")
        try:
            assert isinstance(response, StreamingHttpResponse)
            content = b"".join(cast(Iterable[bytes], response.streaming_content))
        finally:
            response.close()

        self.assertNotIn(b"window.MS_DEBUG = true", content)


def make_user(username: str, role: str | None = None) -> CustomUser:
    """Користувач із заданою роллю (за замовчуванням — звичайний)."""
    user = CustomUser.objects.create_user(
        username=username, email=f"{username}@example.com", password="test-password"
    )
    if role:
        user.role = role
        user.save(update_fields=["role"])
    return user


class BootstrapSeedCommandTests(TestCase):
    def test_seed_normalizes_names_and_syncs_category_attributes(self):
        from apps.core.management.commands.bootstrap_seed import Command

        root = Category.objects.create(name="Каталог")
        child = Category.objects.create(name="підтип", parent=root)
        attribute_definition = {
            "name": "Напруга",
            "kind": "number",
            "unit": "В",
            "required": True,
            "options": [],
            "targets": [("Каталог", "Підтип")],
        }

        command = Command()
        with patch(
            "apps.core.management.commands.bootstrap_seed.ATTRIBUTES",
            [attribute_definition],
        ):
            command.create_categories({"Каталог": ["підтип"]})
            command.create_attributes()

        child.refresh_from_db()
        self.assertEqual(child.name, "Підтип")
        self.assertEqual(child.path, "Каталог › Підтип")
        self.assertEqual(Category.objects.filter(parent=root).count(), 1)
        attribute = Attribute.objects.get(category=child, name="Напруга")
        self.assertEqual(attribute.unit, "В")
        self.assertTrue(attribute.required)

    def test_seed_rerun_preserves_products_and_unfulfillable_downloads_are_inactive(self):
        tree = {
            "Подарункові картки й поповнення": {"Steam": []},
            "Ігрові проєкти й ассети": {"2D-ресурси": ["Іконки"]},
        }
        products = [
            (
                "Download sample",
                ["Ігрові проєкти й ассети", "2D-ресурси", "Іконки"],
                "digital_file",
                "100",
                1,
                "Demo",
                "active",
            ),
            (
                "Hidden download",
                ["Ігрові проєкти й ассети", "2D-ресурси", "Іконки"],
                "digital_file",
                "200",
                1,
                "Demo",
                "hidden",
            ),
        ]
        with (
            patch("apps.core.management.commands.bootstrap_seed.TREE", tree),
            patch("apps.core.management.commands.bootstrap_seed.ATTRIBUTES", []),
            patch("apps.core.management.commands.bootstrap_seed.PRODUCTS", products),
        ):
            call_command("bootstrap_seed", stdout=io.StringIO())
            seller = CustomUser.objects.get(username="bootstrap_seller")
            first_ids = dict(
                Product.objects.filter(seller=seller).values_list("name", "id")
            )

            gift_card = Product.objects.get(name="Steam Classic #1")
            self.assertEqual(gift_card.product_type, "digital_key")
            self.assertEqual(gift_card.brand, "Steam")
            self.assertEqual(
                Product.objects.get(name="Download sample").status, "inactive"
            )
            hidden = Product.objects.get(name="Hidden download")
            self.assertEqual(hidden.status, "inactive")
            self.assertEqual(hidden.hidden_token, "")

            call_command("bootstrap_seed", stdout=io.StringIO())
            second_ids = dict(
                Product.objects.filter(seller=seller).values_list("name", "id")
            )

        self.assertEqual(first_ids, second_ids)
        self.assertEqual(len(second_ids), 6)


class SiteBlocksAdminApiTests(TestCase):
    """Доступ, CRUD, порядок, журнал і валідація блоків головної."""

    def setUp(self):
        self.main = CustomUser.objects.create_superuser(
            username="main", email="main@example.com", password="test-password"
        )
        self.admin1 = make_user("a1", CustomUser.ROLE_ADMIN_1)
        self.admin2 = make_user("a2", CustomUser.ROLE_ADMIN_2)  # без права home
        self.admin2_home = make_user("a2h", CustomUser.ROLE_ADMIN_2)
        AdminRight.objects.create(user=self.admin2_home, right="home")
        self.user = make_user("user")

        self.block_a = SiteBlock.objects.create(key="a", title="Блок A", text="Текст A", order=0)
        self.block_b = SiteBlock.objects.create(key="b", title="Блок B", text="Текст B", order=1)

    def client_for(self, user: CustomUser | None) -> APIClient:
        """Сесійний клієнт (працює middleware бану) або гість."""
        client = APIClient()
        if user is not None:
            client.force_login(user)
        return client

    def test_admin2_without_home_right_gets_403_everywhere(self):
        client = self.client_for(self.admin2)

        self.assertEqual(client.get("/api/admin/site/blocks").status_code, 403)
        self.assertEqual(
            client.post(
                "/api/admin/site/blocks", {"title": "x", "text": "y"}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            client.patch(
                f"/api/admin/site/blocks/{self.block_a.pk}", {"title": "z"}, format="json"
            ).status_code,
            403,
        )
        self.assertEqual(
            client.delete(f"/api/admin/site/blocks/{self.block_a.pk}").status_code, 403
        )
        self.assertEqual(
            client.post(
                "/api/admin/site/blocks/reorder",
                {"ids": [self.block_b.pk, self.block_a.pk]},
                format="json",
            ).status_code,
            403,
        )
        self.assertFalse(AdminLog.objects.exists())

    def test_admin2_with_home_right_can_manage_blocks(self):
        client = self.client_for(self.admin2_home)

        created = client.post(
            "/api/admin/site/blocks",
            {
                "eyebrow": "  Новини  ",
                "title": "  Новий блок  ",
                "text": "  Опис  ",
                "layout": "wide",
                "style": "accent",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        block = created.json()["block"]
        self.assertEqual(block["title"], "Новий блок")
        self.assertEqual(block["text"], "Опис")
        self.assertEqual(block["eyebrow"], "Новини")
        self.assertEqual(block["layout"], "wide")
        self.assertEqual(block["style"], "accent")
        self.assertEqual(block["order"], 2)
        self.assertTrue(block["key"])
        block_id = block["id"]

        updated = client.patch(
            f"/api/admin/site/blocks/{block_id}", {"title": "Змінено"}, format="json"
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["block"]["title"], "Змінено")
        self.assertEqual(updated.json()["block"]["text"], "Опис")

        order = [self.block_b.pk, self.block_a.pk, block_id]
        reordered = client.post("/api/admin/site/blocks/reorder", {"ids": order}, format="json")
        self.assertEqual(reordered.status_code, 200)
        self.assertEqual([item["id"] for item in reordered.json()["blocks"]], order)

        deleted = client.delete(f"/api/admin/site/blocks/{block_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(SiteBlock.objects.filter(pk=block_id).exists())

    def test_admin1_and_main_can_manage_blocks(self):
        for actor in (self.admin1, self.main):
            with self.subTest(role=actor.role):
                client = self.client_for(actor)
                self.assertEqual(client.get("/api/admin/site/blocks").status_code, 200)
                created = client.post(
                    "/api/admin/site/blocks", {"title": "T", "text": "X"}, format="json"
                )
                self.assertEqual(created.status_code, 201)

    def test_regular_user_and_guest_are_rejected(self):
        self.assertEqual(self.client_for(self.user).get("/api/admin/site/blocks").status_code, 403)

        guest = self.client_for(None)
        self.assertIn(guest.get("/api/admin/site/blocks").status_code, (401, 403))
        self.assertIn(
            guest.post(
                "/api/admin/site/blocks", {"title": "T", "text": "X"}, format="json"
            ).status_code,
            (401, 403),
        )

    def test_banned_admin_is_blocked_by_ban_gate(self):
        Ban.objects.create(user=self.admin2_home, issued_by=self.main, source="admin", ends_at=None)

        response = self.client_for(self.admin2_home).get("/api/admin/site/blocks")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "banned")

    def test_actions_are_written_to_admin_log(self):
        client = self.client_for(self.admin2_home)

        created = client.post(
            "/api/admin/site/blocks", {"title": "Журнал", "text": "Текст"}, format="json"
        )
        block_id = created.json()["block"]["id"]
        client.patch(f"/api/admin/site/blocks/{block_id}", {"text": "Оновлено"}, format="json")
        client.post(
            "/api/admin/site/blocks/reorder",
            {"ids": [block_id, self.block_a.pk, self.block_b.pk]},
            format="json",
        )
        client.delete(f"/api/admin/site/blocks/{block_id}")

        actions = list(
            AdminLog.objects.filter(actor=self.admin2_home).values_list("action", flat=True)
        )
        self.assertIn('Створив блок головної "Журнал"', actions)
        self.assertIn('Змінив блок головної "Журнал"', actions)
        self.assertIn("Змінив порядок блоків головної", actions)
        self.assertIn('Видалив блок головної "Журнал"', actions)

    def test_public_blocks_follow_new_order(self):
        client = self.client_for(self.admin2_home)
        client.get("/api/site/blocks")
        order = [self.block_b.pk, self.block_a.pk]
        self.assertEqual(
            client.post("/api/admin/site/blocks/reorder", {"ids": order}, format="json").status_code,
            200,
        )

        public = self.client_for(None).get("/api/site/blocks").json()["blocks"]
        self.assertEqual([item["key"] for item in public], ["b", "a"])
        # Публічний формат без id і незмінний
        self.assertEqual(
            set(public[0]),
            {"key", "eyebrow", "title", "text", "layout", "style", "order"},
        )

    def test_health_is_accessible_to_banned_user_without_accepting_policy(self):
        guest_response = self.client_for(None).get("/health/")
        self.assertEqual(guest_response.status_code, 200)
        self.assertEqual(guest_response.json(), {"status": "ok"})

        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])
        Ban.objects.create(user=self.user, issued_by=self.main, ends_at=None)

        response = self.client_for(self.user).get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_create_main_admin_command_is_idempotent(self):
        password = secrets.token_urlsafe(32)
        variables = {
            "MAIN_ADMIN_NICK": "deploy-main",
            "MAIN_ADMIN_EMAIL": "deploy-main@example.com",
            "MAIN_ADMIN_PASSWORD": password,
        }
        with patch.dict(os.environ, variables):
            call_command("create_main_admin", stdout=io.StringIO())
            call_command("create_main_admin", stdout=io.StringIO())

        created = CustomUser.objects.get(username="deploy-main")
        self.assertEqual(created.role, CustomUser.ROLE_ADMIN_MAIN)
        self.assertTrue(created.is_superuser)
        self.assertTrue(created.check_password(password))

    def test_create_main_admin_command_requires_all_environment_values(self):
        with patch.dict(
            os.environ,
            {
                "MAIN_ADMIN_NICK": "",
                "MAIN_ADMIN_EMAIL": "",
                "MAIN_ADMIN_PASSWORD": "",
            },
        ):
            with self.assertRaises(CommandError):
                call_command("create_main_admin")

    def test_length_and_required_validation(self):
        client = self.client_for(self.admin2_home)

        self.assertEqual(
            client.post(
                "/api/admin/site/blocks", {"title": "a" * 201, "text": "x"}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            client.post(
                "/api/admin/site/blocks", {"title": "t", "text": "x" * 2001}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            client.post(
                "/api/admin/site/blocks", {"title": "   ", "text": "x"}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            client.post("/api/admin/site/blocks", {"title": "t"}, format="json").status_code, 400
        )

    def test_reorder_requires_all_blocks(self):
        client = self.client_for(self.admin2_home)

        partial = client.post(
            "/api/admin/site/blocks/reorder", {"ids": [self.block_a.pk]}, format="json"
        )
        self.assertEqual(partial.status_code, 400)


class RateLimitTests(TestCase):
    def test_login_rate_limit_returns_localized_429(self):
        cache.clear()
        client = APIClient()

        for _ in range(5):
            response = client.post(
                "/api/auth/login",
                {"login": "missing-user", "password": "invalid"},
                format="json",
            )
            self.assertEqual(response.status_code, 400)

        limited = client.post(
            "/api/auth/login",
            {"login": "missing-user", "password": "invalid"},
            format="json",
        )

        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["error"], "Забагато запитів. Спробуйте пізніше.")


class BootstrapAdminTests(TestCase):
    def test_bootstrap_admin_is_created_without_resetting_an_existing_password(self):
        env = {
            "BOOTSTRAP_ADMIN_USERNAME": "boss",
            "BOOTSTRAP_ADMIN_PASSWORD": "20261122",
            "BOOTSTRAP_ADMIN_EMAIL": "boss@example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            call_command("bootstrap_admin")

        boss = CustomUser.objects.get(username="boss")
        self.assertEqual(boss.role, CustomUser.ROLE_ADMIN_MAIN)
        self.assertTrue(boss.check_password("20261122"))

        boss.set_password("changed-password-123")
        boss.save(update_fields=["password"])

        with patch.dict(os.environ, env, clear=False):
            call_command("bootstrap_admin")

        boss.refresh_from_db()
        self.assertTrue(boss.check_password("changed-password-123"))
        self.assertFalse(boss.check_password("20261122"))

    def test_login_accepts_bootstrap_nickname_and_email(self):
        boss = CustomUser.objects.create_user(
            username="boss",
            email="boss@example.com",
            password="20261122",
            role=CustomUser.ROLE_ADMIN_MAIN,
        )

        for login_value in ("boss", "boss@example.com"):
            with self.subTest(login=login_value):
                client = APIClient()
                response = client.post(
                    "/api/auth/login",
                    {"login": login_value, "password": "20261122"},
                    format="json",
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(boss.is_admin_main)

    @override_settings(
        AUTH_PASSWORD_VALIDATORS=[
            {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
        ]
    )
    def test_registration_rejects_a_numeric_password_but_bootstrap_bypasses_validation(self):
        with self.assertRaises(ValidationError):
            password_validation.validate_password("20261122")

        env = {
            "BOOTSTRAP_ADMIN_USERNAME": "boss",
            "BOOTSTRAP_ADMIN_PASSWORD": "20261122",
            "BOOTSTRAP_ADMIN_EMAIL": "boss@example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            call_command("bootstrap_admin")

        boss = CustomUser.objects.get(username="boss")
        self.assertTrue(boss.check_password("20261122"))


class ApiErrorResponseTests(TestCase):
    def test_unhandled_api_exception_returns_a_sanitized_json_response(self):
        request = APIRequestFactory().get("/api/example")

        with self.assertLogs("apps.core.exceptions", level="ERROR"):
            response = api_exception_handler(
                RuntimeError("sensitive exception details"),
                {"request": request},
            )

        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data, {"error": GENERIC_SERVER_ERROR})

    def test_unknown_api_route_does_not_return_a_django_html_page(self):
        response = APIClient().get("/api/unknown-route")

        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.headers["Content-Type"].startswith("application/json"))
        self.assertEqual(response.json(), {"error": "API-маршрут не знайдено."})
