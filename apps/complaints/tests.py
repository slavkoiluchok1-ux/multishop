import json
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.response import Response as APIResponse
from rest_framework.test import APIClient, APIRequestFactory

from apps.accounts.models import (
    AdminLog,
    AdminRight,
    Ban,
    CustomUser,
    SellerVerification,
)
from apps.catalog.models import Category, Product, Review
from apps.notifications.models import Notification
from apps.orders.models import Order, Refund
from apps.wallet.models import EscrowHold, FrozenPayout, Wallet

from .models import Complaint, ComplaintComment, ComplaintEvidence, ComplaintType
from .api_views import ComplaintTypesView
from .services import register_false_complaint


def api_get(client: APIClient, *args: Any, **kwargs: Any) -> APIResponse:
    return cast(APIResponse, client.get(*args, **kwargs))


def api_post(client: APIClient, *args: Any, **kwargs: Any) -> APIResponse:
    return cast(APIResponse, client.post(*args, **kwargs))


def api_data(response: APIResponse) -> dict[str, Any]:
    return cast(dict[str, Any], response.data)


def api_error_fields(response: APIResponse) -> dict[str, Any]:
    """Поля помилки валідації (обгортка проєкту: {"error": ..., "fields": {...}})."""
    return cast(dict[str, Any], api_data(response).get("fields", {}))


@override_settings(DEBUG=True)
class ComplaintTypeSeedTests(TestCase):
    def test_seed_builds_full_tree_and_is_idempotent(self):
        call_command("seed_complaint_types")
        count = ComplaintType.objects.count()
        call_command("seed_complaint_types")

        self.assertEqual(count, 148)
        self.assertEqual(ComplaintType.objects.count(), count)
        admin_type = ComplaintType.objects.get(
            view="users_content",
            path="Адміністратори › Зловживання доступом до фото верифікації",
        )
        self.assertEqual(admin_type.handler_rights, ["admin_1", "admin_main"])

    def test_order_complaint_api_returns_only_leaf_types(self):
        call_command("seed_complaint_types")

        request = APIRequestFactory().get(
            "/api/complaint-types?view=orders&leaves=1"
        )
        response = cast(APIResponse, ComplaintTypesView.as_view()(request))
        data = cast(dict[str, Any], response.data)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["types"])
        self.assertTrue(all(item["is_leaf"] for item in data["types"]))
        expected = ComplaintType.objects.get(
            view="orders",
            path="Фізичні замовлення › Доставка › Не прийшло замовлення",
        )
        row = next(item for item in data["types"] if item["id"] == expected.id)
        self.assertEqual(row["required_fields"], expected.required_fields)


# seed_complaint_types працює лише при DEBUG=True, але тоді вмикається
# debug_toolbar, який ламає тестовий клієнт — тому прибираємо його middleware.
MIDDLEWARE_WITHOUT_TOOLBAR = [
    name for name in settings.MIDDLEWARE if "debug_toolbar" not in name
]


@override_settings(DEBUG=True, MIDDLEWARE=MIDDLEWARE_WITHOUT_TOOLBAR)
class ComplaintFlowTests(TestCase):
    """Подача, перегляд, коментарі, докази й блок скарг користувача (Крок 1)."""

    buyer: CustomUser
    seller: CustomUser
    other: CustomUser
    admin: CustomUser
    product: Product
    order: Order
    review: Review

    def setUp(self):
        cache.clear()
        call_command("seed_complaint_types")
        self.buyer = CustomUser.objects.create_user(
            username="buyer", email="buyer@example.com", password="test-password"
        )
        self.seller = CustomUser.objects.create_user(
            username="seller", email="seller@example.com", password="test-password"
        )
        self.other = CustomUser.objects.create_user(
            username="other", email="other@example.com", password="test-password"
        )
        self.admin = CustomUser.objects.create_user(
            username="a2", email="a2@example.com", password="test-password"
        )
        self.admin.role = CustomUser.ROLE_ADMIN_2
        self.admin.save(update_fields=["role"])
        AdminRight.objects.create(user=self.admin, right="complaints")

        category = Category.objects.create(name="Test category")
        self.product = Product.objects.create(
            seller=self.seller,
            category=category,
            name="Test product",
            product_type="physical",
        )
        self.order = Order.objects.create(
            buyer=self.buyer,
            seller=self.seller,
            total_mc=Decimal("100"),
            status="paid",
        )
        self.review = Review.objects.create(
            product=self.product, author=self.other, text="Негативний відгук"
        )

        self.buyer_client = self.client_for(self.buyer)
        self.seller_client = self.client_for(self.seller)
        self.other_client = self.client_for(self.other)
        self.admin_client = self.client_for(self.admin)

        self.order_type = ComplaintType.objects.get(
            view="orders", path="Фізичні замовлення › Доставка › Не прийшло замовлення"
        )
        self.product_type = ComplaintType.objects.get(
            view="products", path="Заборонений товар › Зброя й боєприпаси"
        )
        self.user_type = ComplaintType.objects.get(
            view="users_content", path="Продавець › Шахрайство"
        )
        self.comment_type = ComplaintType.objects.get(
            view="users_content", path="Коментарі до товару › Образи"
        )

    def tearDown(self):
        for row in ComplaintEvidence.objects.all():
            if row.file.name:
                try:
                    row.file.storage.delete(row.file.name)
                except OSError:
                    # Файл ще відкритий FileResponse-ом — Windows не дає видалити.
                    pass

    @staticmethod
    def client_for(user: CustomUser) -> APIClient:
        client = APIClient()
        client.force_authenticate(user)
        return client

    def submit(self, client, complaint_type, files=None, **target):
        payload: dict[str, Any] = {
            "complaint_type_id": complaint_type.id,
            "text": "Опис проблеми",
            "fields": json.dumps({name: "x" for name in complaint_type.required_fields}),
        }
        payload.update(target)
        if files:
            payload.update(files)
        return api_post(client, "/api/complaints", payload, format="multipart")

    def test_submit_on_order_notifies_seller(self):
        response = self.submit(self.buyer_client, self.order_type, order_id=self.order.id)
        data = api_data(response)

        self.assertEqual(response.status_code, 201, data)
        self.assertEqual(data["target_kind"], "order")
        self.assertEqual(data["target_user"], "seller")
        complaint = Complaint.objects.get(pk=data["id"])
        self.assertEqual(complaint.author, self.buyer)
        self.assertTrue(
            Notification.objects.filter(user=self.seller, section="complaints").exists()
        )

    def test_submit_on_product_uses_seller_as_target(self):
        response = self.submit(
            self.buyer_client, self.product_type, product_id=self.product.id
        )
        data = api_data(response)

        self.assertEqual(response.status_code, 201, data)
        self.assertEqual(data["target_kind"], "product")
        self.assertEqual(data["target_user"], "seller")

    def test_submit_on_user(self):
        response = self.submit(
            self.buyer_client, self.user_type, target_user_id=self.seller.id
        )
        data = api_data(response)

        self.assertEqual(response.status_code, 201, data)
        self.assertEqual(data["target_kind"], "user")
        self.assertEqual(data["target_user"], "seller")

    def test_submit_on_comment(self):
        response = self.submit(
            self.buyer_client, self.comment_type, comment_id=self.review.id
        )
        data = api_data(response)

        self.assertEqual(response.status_code, 201, data)
        self.assertEqual(data["target_kind"], "comment")
        self.assertEqual(data["target_user"], "other")

    def test_foreign_order_is_rejected(self):
        response = self.submit(self.other_client, self.order_type, order_id=self.order.id)

        self.assertEqual(response.status_code, 400, api_data(response))
        self.assertIn("order_id", api_error_fields(response))

    def test_complaint_about_self_product_or_comment_is_rejected(self):
        about_self = self.submit(
            self.buyer_client, self.user_type, target_user_id=self.buyer.id
        )
        self.assertEqual(about_self.status_code, 400)
        self.assertIn("target_user_id", api_error_fields(about_self))

        about_own_product = self.submit(
            self.seller_client, self.product_type, product_id=self.product.id
        )
        self.assertEqual(about_own_product.status_code, 400)
        self.assertIn("product_id", api_error_fields(about_own_product))

        about_own_comment = self.submit(
            self.other_client, self.comment_type, comment_id=self.review.id
        )
        self.assertEqual(about_own_comment.status_code, 400)
        self.assertIn("comment_id", api_error_fields(about_own_comment))

    def test_order_complaint_respects_deadline(self):
        self.order.received_at = timezone.now()
        self.order.complaint_deadline_at = timezone.now() + timedelta(days=1)
        self.order.save(update_fields=["received_at", "complaint_deadline_at"])
        allowed = self.submit(self.buyer_client, self.order_type, order_id=self.order.id)
        self.assertEqual(allowed.status_code, 201, api_data(allowed))

        self.order.complaint_deadline_at = timezone.now() - timedelta(days=1)
        self.order.save(update_fields=["complaint_deadline_at"])
        overdue = self.submit(self.buyer_client, self.order_type, order_id=self.order.id)
        self.assertEqual(overdue.status_code, 400, api_data(overdue))
        self.assertIn("order_id", api_error_fields(overdue))

    def test_type_view_must_match_target(self):
        response = self.submit(self.buyer_client, self.product_type, order_id=self.order.id)

        self.assertEqual(response.status_code, 400, api_data(response))
        self.assertIn("product_id", api_error_fields(response))

    def test_parent_type_is_rejected(self):
        parent = ComplaintType.objects.get(view="products", path="Заборонений товар")
        response = self.submit(
            self.buyer_client, parent, product_id=self.product.id
        )

        self.assertEqual(response.status_code, 400, api_data(response))
        self.assertIn("complaint_type_id", api_error_fields(response))

    def test_admin_with_complaints_right_cannot_submit(self):
        response = self.submit(self.admin_client, self.order_type, order_id=self.order.id)

        self.assertEqual(response.status_code, 403, api_data(response))

    def test_complainant_nick_hidden_from_respondent(self):
        complaint_id = api_data(
            self.submit(self.buyer_client, self.order_type, order_id=self.order.id)
        )["id"]

        own = api_data(api_get(self.buyer_client, f"/api/complaints/{complaint_id}"))
        other = api_data(api_get(self.seller_client, f"/api/complaints/{complaint_id}"))

        self.assertEqual(own["author"], "buyer")
        self.assertEqual(other["author"], "Скаржник")
        self.assertEqual(other["role"], "respondent")

    def test_outsider_cannot_open_complaint(self):
        complaint_id = api_data(
            self.submit(self.buyer_client, self.order_type, order_id=self.order.id)
        )["id"]

        response = api_get(self.other_client, f"/api/complaints/{complaint_id}")

        self.assertEqual(response.status_code, 404)

    def test_evidence_is_private(self):
        proof = SimpleUploadedFile(
            "proof.pdf", b"evidence-bytes", content_type="application/pdf"
        )
        response = self.submit(
            self.buyer_client,
            self.order_type,
            files={"evidence": proof},
            order_id=self.order.id,
        )
        data = api_data(response)
        self.assertEqual(response.status_code, 201, data)
        row = ComplaintEvidence.objects.get(complaint_id=data["id"])

        author_view = api_get(self.buyer_client, f"/api/complaints/evidence/{row.id}")
        respondent_view = api_get(self.seller_client, f"/api/complaints/evidence/{row.id}")
        admin_view = api_get(self.admin_client, f"/api/complaints/evidence/{row.id}")
        self.assertEqual(author_view.status_code, 200)
        self.assertEqual(respondent_view.status_code, 404)
        self.assertEqual(admin_view.status_code, 200)

        respondent_detail = api_data(
            api_get(self.seller_client, f"/api/complaints/{data['id']}")
        )
        self.assertEqual(respondent_detail["evidence"], [])

    def test_admin_detail_and_evidence_respect_complaint_rights(self):
        order_data = api_data(
            self.submit(self.buyer_client, self.order_type, order_id=self.order.id)
        )
        product_data = api_data(
            self.submit(
                self.buyer_client, self.product_type, product_id=self.product.id
            )
        )
        evidence = ComplaintEvidence.objects.create(
            complaint_id=order_data["id"],
            file=SimpleUploadedFile(
                "admin-proof.pdf", b"evidence-bytes", content_type="application/pdf"
            ),
        )

        products_admin = CustomUser.objects.create_user(
            username="products_admin",
            email="products_admin@example.com",
            password="test-password",
            role=CustomUser.ROLE_ADMIN_2,
        )
        AdminRight.objects.create(user=products_admin, right="products")
        products_client = self.client_for(products_admin)

        self.assertEqual(
            api_get(products_client, f"/api/complaints/{order_data['id']}").status_code,
            403,
        )
        self.assertEqual(
            api_get(products_client, f"/api/complaints/{product_data['id']}").status_code,
            200,
        )
        self.assertEqual(
            api_get(
                products_client, f"/api/complaints/evidence/{evidence.id}"
            ).status_code,
            403,
        )
        self.assertEqual(
            api_get(
                self.admin_client, f"/api/complaints/evidence/{evidence.id}"
            ).status_code,
            200,
        )

    def test_comments_blocked_after_resolution(self):
        complaint_id = api_data(
            self.submit(self.buyer_client, self.order_type, order_id=self.order.id)
        )["id"]
        first = api_post(
            self.buyer_client,
            f"/api/complaints/{complaint_id}/comments",
            {"text": "Пояснення"},
            format="json",
        )
        self.assertEqual(first.status_code, 201, api_data(first))

        complaint = Complaint.objects.get(pk=complaint_id)
        complaint.status = "upheld"
        complaint.resolved_at = timezone.now()
        complaint.save(update_fields=["status", "resolved_at"])
        blocked = api_post(
            self.buyer_client,
            f"/api/complaints/{complaint_id}/comments",
            {"text": "Ще одне"},
            format="json",
        )
        self.assertEqual(blocked.status_code, 400, api_data(blocked))

    def test_false_complaints_block_submission(self):
        for _ in range(4):
            register_false_complaint(self.other)

        response = self.submit(self.other_client, self.order_type, order_id=self.order.id)

        self.assertEqual(response.status_code, 403, api_data(response))
        self.assertIn("заблокована", str(api_data(response)))

    def test_tabs_split_own_and_against(self):
        self.submit(self.buyer_client, self.order_type, order_id=self.order.id)

        mine = api_data(api_get(self.buyer_client, "/api/complaints?tab=mine"))
        against = api_data(api_get(self.seller_client, "/api/complaints?tab=against"))

        self.assertEqual(mine["count"], 1)
        self.assertEqual(mine["results"][0]["role"], "author")
        self.assertEqual(against["count"], 1)
        self.assertEqual(against["results"][0]["role"], "respondent")
        self.assertEqual(against["results"][0]["counterparty"], "Скаржник")


# 1x1 PNG — придатний для ImageField верифікації.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000154a24f6f00000000"
    "49454e44ae426082"
)


@override_settings(DEBUG=True, MIDDLEWARE=MIDDLEWARE_WITHOUT_TOOLBAR)
class AdminComplaintTests(TestCase):
    """Розгляд скарг адмінами, наслідки, лічильники й верифікація (Кроки 2–3)."""

    def setUp(self):
        cache.clear()
        call_command("seed_complaint_types")
        self.buyer = self.make_user("buyer")
        self.seller = self.make_user("seller")
        self.other = self.make_user("other")
        self.admin_main = self.make_admin("admin_main", CustomUser.ROLE_ADMIN_MAIN)
        self.admin_1 = self.make_admin("admin_1", CustomUser.ROLE_ADMIN_1)
        self.admin_c = self.make_admin("admin_c", CustomUser.ROLE_ADMIN_2, ["complaints"])
        self.admin_p = self.make_admin("admin_p", CustomUser.ROLE_ADMIN_2, ["products"])
        self.admin_b = self.make_admin("admin_b", CustomUser.ROLE_ADMIN_2, ["bans"])

        category = Category.objects.create(name="Test category")
        self.product = Product.objects.create(
            seller=self.seller,
            category=category,
            name="Test product",
            product_type="physical",
        )
        self.order = Order.objects.create(
            buyer=self.buyer,
            seller=self.seller,
            total_mc=Decimal("100"),
            status="paid",
        )
        self.buyer_wallet = Wallet.objects.create(
            user=self.buyer, held_mc=Decimal("100")
        )
        self.escrow = EscrowHold.objects.create(
            order=self.order,
            buyer=self.buyer,
            seller=self.seller,
            amount_mc=Decimal("100"),
            fee_mc=Decimal("3"),
        )

        self.order_type = ComplaintType.objects.get(
            view="orders", path="Фізичні замовлення › Доставка › Не прийшло замовлення"
        )
        self.product_type = ComplaintType.objects.get(
            view="products", path="Заборонений товар › Зброя й боєприпаси"
        )
        self.user_type = ComplaintType.objects.get(
            view="users_content", path="Продавець › Шахрайство"
        )
        self.admin_type = ComplaintType.objects.get(
            view="users_content", path="Адміністратори › Необґрунтований бан"
        )

        self.order_complaint = self.make_complaint(
            self.buyer,
            self.order_type,
            target_kind="order",
            order=self.order,
            target_user=self.seller,
        )
        self.product_complaint = self.make_complaint(
            self.buyer,
            self.product_type,
            target_kind="product",
            product=self.product,
            target_user=self.seller,
        )
        self.user_complaint = self.make_complaint(
            self.buyer, self.user_type, target_kind="user", target_user=self.seller
        )
        self.admin_complaint = self.make_complaint(
            self.buyer, self.admin_type, target_kind="admin", target_user=self.admin_1
        )

    def tearDown(self):
        for row in SellerVerification.objects.all():
            if row.photo and row.photo.name:
                try:
                    row.photo.storage.delete(row.photo.name)
                except OSError:
                    pass

    # --- Допоміжні ---

    @staticmethod
    def make_user(username: str) -> CustomUser:
        return CustomUser.objects.create_user(
            username=username, email=f"{username}@example.com", password="test-password"
        )

    def make_admin(self, username, role, rights=()) -> CustomUser:
        user = self.make_user(username)
        user.role = role
        user.save(update_fields=["role"])
        for right in rights:
            AdminRight.objects.create(user=user, right=right)
        return user

    @staticmethod
    def make_complaint(author, complaint_type, **kwargs) -> Complaint:
        return Complaint.objects.create(
            author=author, complaint_type=complaint_type, text="Опис проблеми", **kwargs
        )

    @staticmethod
    def client_for(user: CustomUser) -> APIClient:
        client = APIClient()
        client.force_authenticate(user)
        return client

    def group_titles(self, admin) -> set[str]:
        response = api_get(self.client_for(admin), "/api/admin/complaints?view=all")
        self.assertEqual(response.status_code, 200, api_data(response))
        return {group["title"] for group in api_data(response)["results"]}

    def resolve(self, admin, complaint_id, **payload):
        return api_post(
            self.client_for(admin),
            f"/api/admin/complaints/{complaint_id}/resolve",
            payload,
            format="json",
        )

    # --- Видимість за правами й handler_rights ---

    def test_visibility_respects_handler_rights(self):
        order_title = f"Замовлення №{self.order.id}"

        self.assertEqual(self.group_titles(self.admin_p), {"Test product"})
        self.assertEqual(self.group_titles(self.admin_b), {"seller"})
        self.assertEqual(
            self.group_titles(self.admin_c),
            {order_title, "Test product", "seller"},
        )
        # admin_1 не розглядає скаргу на себе; головний бачить і її
        self.assertEqual(
            self.group_titles(self.admin_1), {order_title, "Test product", "seller"}
        )
        self.assertEqual(
            self.group_titles(self.admin_main),
            {order_title, "Test product", "seller", "admin_1"},
        )

    def test_admin_complaints_default_view_includes_all_and_searches_type(self):
        client = self.client_for(self.admin_main)
        response = api_get(client, "/api/admin/complaints")

        self.assertEqual(response.status_code, 200, api_data(response))
        data = api_data(response)
        self.assertEqual(data["view"], "all")
        self.assertIn(f"Замовлення №{self.order.id}", {
            group["title"] for group in data["results"]
        })

        search = api_get(client, "/api/admin/complaints?q=Фізичні замовлення")
        self.assertEqual(search.status_code, 200, api_data(search))
        self.assertEqual(
            {group["title"] for group in api_data(search)["results"]},
            {f"Замовлення №{self.order.id}"},
        )

    def test_admin_cannot_open_complaint_outside_his_rights(self):
        response = api_get(
            self.client_for(self.admin_p),
            f"/api/admin/complaints/{self.order_complaint.id}",
        )
        self.assertEqual(response.status_code, 403)
        # скаргу на адміна 1-го рівня admin_c не бачить навіть з правом complaints
        response = api_get(
            self.client_for(self.admin_c),
            f"/api/admin/complaints/{self.admin_complaint.id}",
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_does_not_review_complaints_about_himself(self):
        about_c = self.make_complaint(
            self.other, self.user_type, target_kind="user", target_user=self.admin_c
        )
        about_other_admin = self.make_complaint(
            self.other, self.user_type, target_kind="user", target_user=self.admin_p
        )
        by_c = self.make_complaint(
            self.admin_c, self.user_type, target_kind="user", target_user=self.seller
        )

        self.assertEqual(
            api_get(
                self.client_for(self.admin_c),
                f"/api/admin/complaints/{about_c.id}",
            ).status_code,
            403,
        )
        # скаргу на admin_2 розглядає лише admin_1 або головний
        self.assertEqual(
            api_get(
                self.client_for(self.admin_c),
                f"/api/admin/complaints/{about_other_admin.id}",
            ).status_code,
            403,
        )
        self.assertEqual(
            api_get(
                self.client_for(self.admin_c),
                f"/api/admin/complaints/{by_c.id}",
            ).status_code,
            403,
        )
        self.assertEqual(
            api_get(
                self.client_for(self.admin_1),
                f"/api/admin/complaints/{about_other_admin.id}",
            ).status_code,
            200,
        )

    # --- Взяти в роботу, коментар ---

    def test_take_moves_to_in_review(self):
        client = self.client_for(self.admin_c)
        response = api_post(
            client, f"/api/admin/complaints/{self.order_complaint.id}/take"
        )
        self.assertEqual(response.status_code, 200, api_data(response))
        self.assertEqual(api_data(response)["status"], "in_review")

        complaint = Complaint.objects.get(pk=self.order_complaint.id)
        self.assertEqual(complaint.handled_by, self.admin_c)
        self.assertTrue(AdminLog.objects.filter(actor=self.admin_c).exists())

        again = api_post(
            client, f"/api/admin/complaints/{self.order_complaint.id}/take"
        )
        self.assertEqual(again.status_code, 400, api_data(again))

    def test_admin_comment_notifies_both_sides(self):
        response = api_post(
            self.client_for(self.admin_c),
            f"/api/admin/complaints/{self.order_complaint.id}/comments",
            {"text": "Розглядаємо"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, api_data(response))
        self.assertEqual(api_data(response)["side"], "admin")
        self.assertTrue(
            Notification.objects.filter(user=self.buyer, section="complaints").exists()
        )
        self.assertTrue(
            Notification.objects.filter(user=self.seller, section="complaints").exists()
        )

    # --- Наслідки ---

    def test_refund_full(self):
        full = self.resolve(
            self.admin_c,
            self.order_complaint.id,
            status="upheld",
            consequences=["refund"],
        )
        self.assertEqual(full.status_code, 200, api_data(full))
        self.buyer_wallet.refresh_from_db()
        self.escrow.refresh_from_db()
        self.assertEqual(self.buyer_wallet.free_balance_mc, Decimal("100.000000"))
        self.assertEqual(self.buyer_wallet.held_mc, Decimal("0.000000"))
        self.assertEqual(self.escrow.status, "refunded")
        self.assertTrue(
            Refund.objects.filter(order=self.order, amount_mc=Decimal("100")).exists()
        )
        refund = Refund.objects.get(order=self.order)
        self.assertEqual(refund.fee_returned_mc, Decimal("0"))
        self.assertEqual(refund.source, "escrow")
        complaint = Complaint.objects.get(pk=self.order_complaint.id)
        self.assertEqual(complaint.status, "upheld")
        # повне повернення — сума не вказується, поле лишається порожнім
        self.assertIsNone(complaint.refund_amount_mc)

    def test_refund_after_payout_debits_frozen_money_and_deposit(self):
        self.escrow.status = "released"
        self.escrow.resolved_at = timezone.now()
        self.escrow.save(update_fields=["status", "resolved_at"])
        self.buyer_wallet.held_mc = Decimal("0")
        self.buyer_wallet.save(update_fields=["held_mc"])
        seller_wallet = Wallet.objects.create(
            user=self.seller,
            frozen_mc=Decimal("97"),
            deposit_locked_mc=Decimal("3"),
        )
        payout = FrozenPayout.objects.create(
            seller=self.seller,
            order=self.order,
            amount_mc=Decimal("97"),
            release_at=timezone.now() + timedelta(days=14),
        )

        response = self.resolve(
            self.admin_c,
            self.order_complaint.id,
            status="upheld",
            consequences=["refund"],
        )

        self.assertEqual(response.status_code, 200, api_data(response))
        self.buyer_wallet.refresh_from_db()
        seller_wallet.refresh_from_db()
        payout.refresh_from_db()
        refund = Refund.objects.get(order=self.order)
        self.assertEqual(self.buyer_wallet.free_balance_mc, Decimal("100"))
        self.assertEqual(seller_wallet.frozen_mc, Decimal("0"))
        self.assertEqual(seller_wallet.deposit_locked_mc, Decimal("0"))
        self.assertEqual(seller_wallet.debt_mc, Decimal("0"))
        self.assertEqual(payout.status, "used_for_refund")
        self.assertEqual(refund.amount_mc, Decimal("100"))
        self.assertEqual(refund.fee_returned_mc, Decimal("0"))
        self.assertEqual(refund.source, "deposit")
        self.assertEqual(refund.debt_mc, Decimal("0"))

    def test_refund_shortfall_becomes_seller_debt(self):
        self.escrow.status = "released"
        self.escrow.resolved_at = timezone.now()
        self.escrow.save(update_fields=["status", "resolved_at"])
        self.buyer_wallet.held_mc = Decimal("0")
        self.buyer_wallet.save(update_fields=["held_mc"])
        seller_wallet = Wallet.objects.create(
            user=self.seller,
            frozen_mc=Decimal("97"),
        )
        FrozenPayout.objects.create(
            seller=self.seller,
            order=self.order,
            amount_mc=Decimal("97"),
            release_at=timezone.now() + timedelta(days=14),
        )

        response = self.resolve(
            self.admin_c,
            self.order_complaint.id,
            status="upheld",
            consequences=["refund"],
        )

        self.assertEqual(response.status_code, 200, api_data(response))
        self.buyer_wallet.refresh_from_db()
        seller_wallet.refresh_from_db()
        refund = Refund.objects.get(order=self.order)
        self.assertEqual(self.buyer_wallet.free_balance_mc, Decimal("100"))
        self.assertEqual(seller_wallet.frozen_mc, Decimal("0"))
        self.assertEqual(seller_wallet.debt_mc, Decimal("3"))
        self.assertEqual(refund.fee_returned_mc, Decimal("0"))
        self.assertEqual(refund.debt_mc, Decimal("3"))

    def test_refund_partial_returns_requested_amount(self):
        order = Order.objects.create(
            buyer=self.buyer,
            seller=self.seller,
            total_mc=Decimal("100"),
            status="paid",
        )
        EscrowHold.objects.create(
            order=order,
            buyer=self.buyer,
            seller=self.seller,
            amount_mc=Decimal("100"),
            fee_mc=Decimal("3"),
        )
        complaint = self.make_complaint(
            self.buyer,
            self.order_type,
            target_kind="order",
            order=order,
            target_user=self.seller,
        )

        response = self.resolve(
            self.admin_c,
            complaint.id,
            status="upheld",
            consequences=["refund"],
            refund_amount_mc="40",
        )
        self.assertEqual(response.status_code, 200, api_data(response))
        self.buyer_wallet.refresh_from_db()
        self.assertEqual(self.buyer_wallet.free_balance_mc, Decimal("40.000000"))
        self.assertEqual(self.buyer_wallet.held_mc, Decimal("60.000000"))
        complaint.refresh_from_db()
        self.assertEqual(complaint.refund_amount_mc, Decimal("40.000000"))

    def test_pay_seller_freezes_payout(self):
        response = self.resolve(
            self.admin_c,
            self.order_complaint.id,
            status="upheld",
            consequences=["pay_seller"],
        )
        self.assertEqual(response.status_code, 200, api_data(response))
        seller_wallet = Wallet.objects.get(user=self.seller)
        self.assertEqual(seller_wallet.frozen_mc, Decimal("97.000000"))
        payout = FrozenPayout.objects.get(seller=self.seller)
        self.assertEqual(payout.amount_mc, Decimal("97.000000"))
        self.assertGreater(payout.release_at, timezone.now())
        self.escrow.refresh_from_db()
        self.assertEqual(self.escrow.status, "released")

    def test_cancel_order_consequence(self):
        response = self.resolve(
            self.admin_c,
            self.order_complaint.id,
            status="upheld",
            consequences=["cancel_order"],
        )
        self.assertEqual(response.status_code, 200, api_data(response))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "cancelled")

    def test_multiple_consequences_at_once(self):
        response = self.resolve(
            self.admin_c,
            self.order_complaint.id,
            status="upheld",
            consequences=["warning", "refund"],
        )
        self.assertEqual(response.status_code, 200, api_data(response))
        complaint = Complaint.objects.get(pk=self.order_complaint.id)
        self.assertEqual(complaint.consequences, ["warning", "refund"])
        self.buyer_wallet.refresh_from_db()
        self.assertEqual(self.buyer_wallet.free_balance_mc, Decimal("100.000000"))
        # попередження надходить відповідачу — продавцю
        self.assertTrue(
            Notification.objects.filter(user=self.seller, section="complaints").exists()
        )

    def test_consequence_requires_right(self):
        no_orders = self.resolve(
            self.admin_p,
            self.product_complaint.id,
            status="upheld",
            consequences=["refund"],
        )
        self.assertEqual(no_orders.status_code, 403)
        no_bans = self.resolve(
            self.admin_p,
            self.product_complaint.id,
            status="upheld",
            consequences=["ban"],
        )
        self.assertEqual(no_bans.status_code, 403)
        no_products = self.resolve(
            self.admin_b,
            self.user_complaint.id,
            status="upheld",
            consequences=["hide_product"],
        )
        self.assertEqual(no_products.status_code, 403)

        self.product_complaint.refresh_from_db()
        self.assertEqual(self.product_complaint.status, "new")

    def test_ban_consequence_creates_ban(self):
        no_right = self.resolve(
            self.admin_c,
            self.user_complaint.id,
            status="upheld",
            consequences=["ban"],
        )
        self.assertEqual(no_right.status_code, 403)

        response = self.resolve(
            self.admin_b,
            self.user_complaint.id,
            status="upheld",
            consequences=["ban"],
            ban_duration="7d",
        )
        self.assertEqual(response.status_code, 200, api_data(response))
        ban = Ban.objects.get(user=self.seller)
        self.assertEqual(ban.source, "complaint")
        self.assertEqual(ban.complaint_id, self.user_complaint.id)

    def test_restrict_complaints_requires_admin1(self):
        denied = self.resolve(
            self.admin_c,
            self.user_complaint.id,
            status="upheld",
            consequences=["restrict_complaints"],
        )
        self.assertEqual(denied.status_code, 403)

        allowed = self.resolve(
            self.admin_1,
            self.user_complaint.id,
            status="upheld",
            consequences=["restrict_complaints"],
        )
        self.assertEqual(allowed.status_code, 200, api_data(allowed))
        self.seller.profile.refresh_from_db()
        self.assertIsNotNone(self.seller.profile.complaints_blocked_until)

    def test_revoke_rights_requires_admin1(self):
        complaint = self.make_complaint(
            self.other, self.user_type, target_kind="user", target_user=self.admin_p
        )
        allowed = self.resolve(
            self.admin_1,
            complaint.id,
            status="upheld",
            consequences=["revoke_rights"],
        )
        self.assertEqual(allowed.status_code, 200, api_data(allowed))
        self.admin_p.refresh_from_db()
        self.assertEqual(self.admin_p.role, CustomUser.ROLE_USER)

    # --- Хибні скарги, зняття блоку ---

    def test_false_status_increments_and_blocks(self):
        profile = self.buyer.profile
        profile.false_complaints_count = 3
        profile.save(update_fields=["false_complaints_count"])

        response = self.resolve(
            self.admin_c, self.order_complaint.id, status="false"
        )
        self.assertEqual(response.status_code, 200, api_data(response))
        profile.refresh_from_db()
        self.assertEqual(profile.false_complaints_count, 0)
        self.assertEqual(profile.complaints_block_count, 1)
        self.assertIsNotNone(profile.complaints_blocked_until)
        self.assertEqual(
            Complaint.objects.get(pk=self.order_complaint.id).status, "false"
        )

    def test_false_status_increments_counter_below_limit(self):
        response = self.resolve(
            self.admin_c, self.product_complaint.id, status="false"
        )
        self.assertEqual(response.status_code, 200, api_data(response))
        self.buyer.profile.refresh_from_db()
        self.assertEqual(self.buyer.profile.false_complaints_count, 1)
        self.assertIsNone(self.buyer.profile.complaints_blocked_until)

    def test_lift_complaint_block_only_admin1(self):
        profile = self.seller.profile
        profile.complaints_blocked_until = timezone.now() + timedelta(days=10)
        profile.false_complaints_count = 2
        profile.save(
            update_fields=["complaints_blocked_until", "false_complaints_count"]
        )

        denied = api_post(
            self.client_for(self.admin_c),
            f"/api/admin/users/{self.seller.id}/lift-complaint-block",
        )
        self.assertEqual(denied.status_code, 403)

        allowed = api_post(
            self.client_for(self.admin_1),
            f"/api/admin/users/{self.seller.id}/lift-complaint-block",
        )
        self.assertEqual(allowed.status_code, 200, api_data(allowed))
        profile.refresh_from_db()
        self.assertIsNone(profile.complaints_blocked_until)
        self.assertEqual(profile.false_complaints_count, 0)

    def test_resolution_notifies_and_logs(self):
        response = self.resolve(
            self.admin_c,
            self.user_complaint.id,
            status="rejected",
            resolution="Немає підстав",
        )
        self.assertEqual(response.status_code, 200, api_data(response))
        complaint = Complaint.objects.get(pk=self.user_complaint.id)
        self.assertEqual(complaint.resolution, "Немає підстав")
        self.assertTrue(AdminLog.objects.filter(actor=self.admin_c).exists())
        self.assertTrue(
            Notification.objects.filter(user=self.buyer, section="complaints").exists()
        )
        self.assertTrue(
            Notification.objects.filter(user=self.seller, section="complaints").exists()
        )

    # --- Крок 3: верифікація й лічильники ---

    def test_verification_photo_requires_right(self):
        photo = SimpleUploadedFile("doc.png", PNG_BYTES, content_type="image/png")
        SellerVerification.objects.create(user=self.seller, photo=photo, age=27)

        data = api_get(
            self.client_for(self.admin_c),
            f"/api/admin/verifications/{self.seller.id}",
        )
        self.assertEqual(data.status_code, 200, api_data(data))
        self.assertEqual(api_data(data)["age"], 27)
        self.assertTrue(api_data(data)["has_photo"])

        photo_view = api_get(
            self.client_for(self.admin_c),
            f"/api/admin/verifications/{self.seller.id}/photo",
        )
        self.assertEqual(photo_view.status_code, 200)

        for actor in (self.admin_p, self.buyer, self.admin_b):
            denied = api_get(
                self.client_for(actor),
                f"/api/admin/verifications/{self.seller.id}",
            )
            self.assertEqual(denied.status_code, 403)
            denied_photo = api_get(
                self.client_for(actor),
                f"/api/admin/verifications/{self.seller.id}/photo",
            )
            self.assertEqual(denied_photo.status_code, 403)

        # кожен перегляд фото пише в AdminLog
        self.assertTrue(
            AdminLog.objects.filter(actor=self.admin_c, target=self.seller).exists()
        )

    def test_counters_expose_admin_complaints_only_to_admins(self):
        counters = api_data(
            api_get(self.client_for(self.admin_c), "/api/notifications/counters")
        )["counters"]
        self.assertEqual(counters["admin_complaints"], 3)

        main_counters = api_data(
            api_get(self.client_for(self.admin_main), "/api/notifications/counters")
        )["counters"]
        self.assertEqual(main_counters["admin_complaints"], 4)

        user_counters = api_data(
            api_get(self.client_for(self.buyer), "/api/notifications/counters")
        )["counters"]
        self.assertNotIn("admin_complaints", user_counters)

        # пул спільний: цифра зменшується, коли скаргу закрито
        self.resolve(self.admin_c, self.order_complaint.id, status="rejected")
        after = api_data(
            api_get(self.client_for(self.admin_c), "/api/notifications/counters")
        )["counters"]
        self.assertEqual(after["admin_complaints"], 2)
