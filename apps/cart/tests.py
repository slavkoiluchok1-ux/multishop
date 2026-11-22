from decimal import Decimal
from typing import Any, cast

from django.test import TestCase
from rest_framework.response import Response as APIResponse
from rest_framework.test import APIClient

from apps.accounts.models import CustomUser
from apps.catalog.models import Category, Product, ProductVariant

from .models import Cart


def api_post(client: APIClient, *args: Any, **kwargs: Any) -> APIResponse:
    return cast(APIResponse, client.post(*args, **kwargs))


def api_response_data(response: APIResponse) -> dict[str, Any]:
    return cast(dict[str, Any], response.data)


class CartAvailabilityTests(TestCase):
    """Кошик приймає active і hidden, відхиляє inactive та sold_out."""

    def setUp(self):
        self.seller = CustomUser.objects.create_user(
            username="cart-seller",
            email="cart@example.com",
            password="test-password",
        )
        category = Category.objects.create(name="Cart category")
        self.product = Product.objects.create(
            seller=self.seller,
            category=category,
            name="Cart product",
            product_type="physical",
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="CART-1", price_mc=Decimal("10"), stock=3
        )
        self.client_api = APIClient()

    def add_to_cart(self) -> APIResponse:
        return api_post(
            self.client_api,
            "/api/cart/items",
            {"variant_id": self.variant.id, "quantity": 1},
            format="json",
        )

    def test_hidden_product_can_be_added(self):
        self.product.status = "hidden"
        self.product.save(update_fields=["status"])

        response = self.add_to_cart()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(api_response_data(response)["count"], 1)

    def test_inactive_product_is_rejected(self):
        self.product.status = "inactive"
        self.product.save(update_fields=["status"])

        response = self.add_to_cart()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            api_response_data(response)["error"], "Товар недоступний для купівлі."
        )

    def test_sold_out_product_reports_empty_stock(self):
        self.product.status = "sold_out"
        self.product.save(update_fields=["status"])

        response = self.add_to_cart()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(api_response_data(response)["error"], "Товар закінчився.")

    def test_guest_cart_merges_after_login_rotates_the_session_key(self):
        shopper = CustomUser.objects.create_user(
            username="cart-shopper",
            email="shopper@example.com",
            password="test-password-123",
        )
        guest_client = APIClient()
        add_response = api_post(
            guest_client,
            "/api/cart/items",
            {"variant_id": self.variant.id, "quantity": 2},
            format="json",
        )
        self.assertEqual(add_response.status_code, 200, add_response.data)
        old_session_key = guest_client.cookies["sessionid"].value
        self.assertTrue(Cart.objects.filter(session_key=old_session_key).exists())

        login_response = guest_client.post(
            "/api/auth/login",
            {"login": shopper.username, "password": "test-password-123"},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200, login_response.data)
        self.assertNotEqual(guest_client.cookies["sessionid"].value, old_session_key)

        merge_response = guest_client.post("/api/cart/merge", {}, format="json")

        self.assertEqual(merge_response.status_code, 200, merge_response.data)
        user_cart = Cart.objects.get(user=shopper)
        self.assertEqual(user_cart.items.get(variant=self.variant).quantity, 2)
        self.assertFalse(Cart.objects.filter(session_key=old_session_key).exists())
