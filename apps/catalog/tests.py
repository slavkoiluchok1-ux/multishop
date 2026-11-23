import json
from decimal import Decimal
from typing import Any, cast

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.response import Response as APIResponse
from rest_framework.test import APIClient

from apps.accounts.models import AdminLog, AdminRight, CustomUser, SellerVerification
from apps.cart.models import Cart, CartItem
from apps.notifications.models import Notification
from apps.orders.models import Order, OrderItem
from apps.wallet.models import FrozenPayout, Wallet, WalletTransaction
from apps.wallet.services import recalculate_seller_deposit, seller_deposit_required

from .models import (
    Attribute,
    AttributeOption,
    Category,
    DigitalFile,
    Product,
    ProductVariant,
    Review,
    ReviewMedia,
    VariantAttributeValue,
)
from apps.orders.models import ActivationCode, activation_code_key


def api_post(client: APIClient, *args: Any, **kwargs: Any) -> APIResponse:
    return cast(APIResponse, client.post(*args, **kwargs))


def api_get(client: APIClient, *args: Any, **kwargs: Any) -> APIResponse:
    return cast(APIResponse, client.get(*args, **kwargs))


def api_patch(client: APIClient, *args: Any, **kwargs: Any) -> APIResponse:
    return cast(APIResponse, client.patch(*args, **kwargs))


def api_response_data(response: APIResponse) -> dict[str, Any]:
    return cast(dict[str, Any], response.data)


class CatalogPopupTests(TestCase):
    phone_leaf: Category
    case_leaf: Category
    compatibility: Attribute

    def setUp(self):
        electronics = Category.objects.create(name="Електроніка")
        phones = Category.objects.create(name="Смартфони й зв'язок", parent=electronics)
        self.phone_leaf = Category.objects.create(name="Смартфони", parent=phones)
        Category.objects.create(name="Android", parent=self.phone_leaf, is_active=False)
        Attribute.objects.create(
            category=self.phone_leaf,
            name="Тип смартфона",
            kind="multiple",
            order=1,
        )
        Attribute.objects.create(
            category=self.phone_leaf,
            name="Конструкція",
            kind="single",
            order=2,
        )

        apparel = Category.objects.create(name="Одяг, взуття, аксесуари")
        accessories = Category.objects.create(name="Аксесуари", parent=apparel)
        device_accessories = Category.objects.create(
            name="Аксесуари для пристроїв", parent=accessories
        )
        self.case_leaf = Category.objects.create(
            name="Чохли й накладки", parent=device_accessories
        )
        self.compatibility = Attribute.objects.create(
            category=device_accessories,
            name="Сумісність",
            kind="multiple",
            order=1,
        )
        Attribute.objects.create(
            category=device_accessories,
            name="Функція",
            kind="single",
            order=2,
        )

    def popup(self, category: Category) -> dict[str, Any]:
        client = cast(APIClient, self.client)
        return api_response_data(
            api_get(client, f"/api/catalog/categories/{category.id}/popup")
        )

    def test_category_map_cache_is_invalidated_on_category_create(self):
        from .api_views import _category_map

        cache.clear()
        cached = _category_map()
        if cached is None:
            self.fail("Мапа категорій не була кешована.")
        category = Category.objects.create(name="Новий розділ")
        updated = _category_map()
        if updated is None:
            self.fail("Мапа категорій не була оновлена.")

        self.assertNotIn(category.id, cached)
        self.assertIn(category.id, updated)

    def test_phone_leaf_shows_filters_instead_of_legacy_subtypes(self):
        data = self.popup(self.phone_leaf)

        self.assertEqual(data["children"], [])
        self.assertEqual(
            [attribute["name"] for attribute in data["attributes"]],
            ["Бренд", "Тип смартфона", "Конструкція"],
        )

    def test_accessory_filters_are_shown_only_at_leaf(self):
        intermediate = Category.objects.get(name="Аксесуари для пристроїв")
        parent_data = self.popup(intermediate)
        leaf_data = self.popup(self.case_leaf)

        self.assertEqual(parent_data["attributes"], [])
        self.assertEqual(
            [attribute["name"] for attribute in leaf_data["attributes"]],
            ["Сумісність", "Функція"],
        )
        self.assertEqual(
            leaf_data["attributes"][0]["id"],
            self.compatibility.id,
        )

    def test_portfolio_is_a_product_link_not_a_boolean_variant_attribute(self):
        services = Category.objects.create(name="Послуги")
        service_leaf = Category.objects.create(name="Дизайн", parent=services)
        Attribute.objects.create(
            category=services,
            name="Є портфоліо",
            kind="bool",
        )

        data = self.popup(service_leaf)

        self.assertNotIn(
            "Є портфоліо",
            [attribute["name"] for attribute in data["attributes"]],
        )

    def test_game_popup_hides_obsolete_platform_and_genre_attributes(self):
        games = Category.objects.create(name="Ігри")
        pc = Category.objects.create(name="PC", parent=games)
        steam = Category.objects.create(name="Steam", parent=pc)
        indie = Category.objects.create(name="Інді", parent=steam)
        Attribute.objects.create(category=games, name="Платформа", kind="single")
        Attribute.objects.create(category=games, name="Жанр", kind="multiple")
        Attribute.objects.create(category=games, name="Мова", kind="multiple")
        Attribute.objects.create(category=games, name="Вікове обмеження", kind="single")
        Attribute.objects.create(category=games, name="Рік релізу", kind="number")
        Attribute.objects.create(category=games, name="Режим", kind="multiple")
        Attribute.objects.create(category=games, name="Регіон ключа", kind="single")
        Attribute.objects.create(category=games, name="Видача", kind="single")
        Attribute.objects.create(category=pc, name="Мова", kind="multiple")
        Attribute.objects.create(category=pc, name="Вікове обмеження", kind="single")

        data = self.popup(indie)
        names = [attribute["name"] for attribute in data["attributes"]]

        self.assertSetEqual(
            set(names),
            {"Регіон ключа", "Видача", "Мова", "Вікове обмеження"},
        )

        add_ons = Category.objects.create(name="Додатки", parent=games)
        items = Category.objects.create(name="Ігрові предмети й паки", parent=add_ons)
        add_on_names = [
            attribute["name"]
            for attribute in self.popup(items)["attributes"]
        ]

        self.assertSetEqual(set(add_on_names), {"Регіон ключа", "Видача"})

    def test_subscription_popup_includes_delivery_and_plan_attributes(self):
        subscriptions = Category.objects.create(name="Підписки")
        games = Category.objects.create(name="Ігрові", parent=subscriptions)
        game_pass = Category.objects.create(name="Game Pass", parent=games)
        for name, options, required in (
            ("Період", ["Місяць", "Рік"], True),
            ("Регіон", ["Глобальний", "Європа"], False),
            ("Тариф", ["Індивідуальний", "Сімейний"], False),
            ("Видача", ["Ключ", "Код активації"], True),
        ):
            attribute = Attribute.objects.create(
                category=subscriptions,
                name=name,
                kind="single",
                required=required,
            )
            for value in options:
                AttributeOption.objects.create(attribute=attribute, value=value)

        data = self.popup(game_pass)

        fields = {attribute["name"]: attribute for attribute in data["attributes"]}
        self.assertSetEqual(set(fields), {"Період", "Регіон", "Тариф", "Видача"})
        self.assertTrue(fields["Видача"]["required"])
        self.assertIn(
            "Код активації",
            [option["value"] for option in fields["Видача"]["options"]],
        )
        self.assertNotIn(
            "Акаунт",
            [option["value"] for option in fields["Видача"]["options"]],
        )
        self.assertNotIn(
            "Посилання-активація",
            [option["value"] for option in fields["Видача"]["options"]],
        )


class SellerProductApiTests(TestCase):
    seller: CustomUser
    category: Category
    api_client: APIClient
    payload: dict[str, Any]

    def setUp(self):
        cache.clear()
        self.seller = CustomUser.objects.create_user(
            username="verified-seller",
            email="seller@example.com",
            password="test-password",
        )
        self.category = Category.objects.create(name="Seller products")
        self.api_client = APIClient()
        self.api_client.force_authenticate(self.seller)
        self.payload = {
            "category_id": self.category.id,
            "name": "New listing",
            "description": "Product description",
            "product_type": "physical",
            "status": "active",
            "variants": [
                {
                    "sku": "LISTING-1",
                    "price_mc": "100",
                    "stock": 5,
                    "attributes": [],
                }
            ],
        }

    def verify_seller(self, balance="20"):
        SellerVerification.objects.create(
            user=self.seller,
            age=25,
            photo="verification/seller.png",
        )
        return Wallet.objects.create(
            user=self.seller,
            free_balance_mc=Decimal(balance),
        )

    def test_product_creation_requires_seller_verification(self):
        response = api_post(
            self.api_client,
            "/api/products/mine", self.payload, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())

    def test_product_creation_locks_required_deposit(self):
        wallet = self.verify_seller()

        response = api_post(
            self.api_client,
            "/api/products/mine", self.payload, format="json"
        )

        self.assertEqual(response.status_code, 201, response.data)
        wallet.refresh_from_db()
        self.assertEqual(wallet.free_balance_mc, Decimal("15"))
        self.assertEqual(wallet.deposit_locked_mc, Decimal("5"))
        self.assertEqual(Product.objects.filter(seller=self.seller).count(), 1)

    def test_seller_with_decimal_debt_cannot_publish_active_product(self):
        wallet = self.verify_seller()
        wallet.debt_mc = Decimal("1.234567")
        wallet.save(update_fields=["debt_mc"])

        response = api_post(
            self.api_client,
            "/api/products/mine",
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("wallet", response.data["fields"])
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())

    def test_portfolio_url_is_saved_and_exposed_to_buyers(self):
        self.verify_seller()
        payload = dict(self.payload)
        payload["portfolio_url"] = "https://example.com/my-work"

        response = api_post(
            self.api_client,
            "/api/products/mine",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        response_data = api_response_data(response)
        self.assertEqual(response_data["portfolio_url"], payload["portfolio_url"])
        product_id = response_data["id"]
        detail = api_get(self.api_client, f"/api/products/{product_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            api_response_data(detail)["portfolio_url"],
            payload["portfolio_url"],
        )

    def test_portfolio_url_can_be_updated_with_partial_patch(self):
        self.verify_seller()
        create_response = api_post(
            self.api_client,
            "/api/products/mine",
            self.payload,
            format="json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        create_data = api_response_data(create_response)

        portfolio_url = "https://example.com/updated-work"
        response = api_patch(
            self.api_client,
            f"/api/products/mine/{create_data['id']}",
            {"portfolio_url": portfolio_url},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(api_response_data(response)["portfolio_url"], portfolio_url)
        product = Product.objects.get(pk=create_data["id"])
        self.assertEqual(product.portfolio_url, portfolio_url)

    def test_portfolio_url_rejects_non_http_schemes(self):
        self.verify_seller()
        payload = dict(self.payload)
        payload["portfolio_url"] = "ftp://example.com/my-work"

        response = api_post(
            self.api_client,
            "/api/products/mine",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())

    def test_multipart_product_creation_parses_variant_json(self):
        self.verify_seller()
        payload = dict(self.payload)
        payload["variants"] = json.dumps(payload["variants"])

        response = api_post(
            self.api_client,
            "/api/products/mine",
            payload,
            format="multipart",
        )

        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(seller=self.seller)
        self.assertEqual(product.variants.count(), 1)
        variant = product.variants.get()
        self.assertEqual(str(variant.price_mc), "100.000000")

    def test_product_type_must_match_category_root(self):
        self.verify_seller()
        games = Category.objects.create(name="Ігри")
        game_leaf = Category.objects.create(name="Action", parent=games)
        payload = dict(self.payload)
        payload["category_id"] = game_leaf.id

        response = api_post(
            self.api_client,
            "/api/products/mine",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())

    def test_development_listing_is_limited_to_one_variant_and_one_sale(self):
        self.verify_seller()
        services = Category.objects.create(name="Послуги")
        development = Category.objects.create(name="Розробка", parent=services)
        web = Category.objects.create(name="Веб", parent=development)
        payload = dict(self.payload)
        payload["category_id"] = web.id
        payload["product_type"] = "service"
        payload["variants"] = [
            {
                "sku": "DEV-1",
                "price_mc": "100",
                "stock": 2,
                "attributes": [],
            }
        ]

        response = api_post(
            self.api_client,
            "/api/products/mine",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())

    def test_insufficient_deposit_rolls_back_product_creation(self):
        wallet = self.verify_seller(balance="2")

        response = api_post(
            self.api_client,
            "/api/products/mine", self.payload, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())
        wallet.refresh_from_db()
        self.assertEqual(wallet.free_balance_mc, Decimal("2"))
        self.assertEqual(wallet.deposit_locked_mc, Decimal("0"))

    def test_hidden_product_locks_deposit(self):
        wallet = self.verify_seller()
        payload = dict(self.payload)
        payload["status"] = "hidden"
        payload["variants"] = [
            {"sku": "HIDDEN-1", "price_mc": "100", "stock": 1, "attributes": []}
        ]

        response = api_post(self.api_client, "/api/products/mine", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("1"))
        self.assertEqual(wallet.free_balance_mc, Decimal("18"))
        self.assertTrue(
            WalletTransaction.objects.filter(
                wallet=wallet,
                kind="hidden_listing_fee",
                amount_mc=Decimal("-1"),
            ).exists()
        )

    def test_hidden_listing_fee_is_charged_once_when_status_changes(self):
        wallet = self.verify_seller()
        response = api_post(
            self.api_client,
            "/api/products/mine",
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        product_id = response.data["id"]
        response = api_patch(
            self.api_client,
            f"/api/products/mine/{product_id}",
            {"status": "hidden"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        wallet.refresh_from_db()
        self.assertEqual(wallet.free_balance_mc, Decimal("14"))
        self.assertEqual(
            WalletTransaction.objects.filter(
                wallet=wallet, kind="hidden_listing_fee"
            ).count(),
            1,
        )

        response = api_patch(
            self.api_client,
            f"/api/products/mine/{product_id}",
            {"status": "active"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        response = api_patch(
            self.api_client,
            f"/api/products/mine/{product_id}",
            {"status": "hidden"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        wallet.refresh_from_db()
        self.assertEqual(wallet.free_balance_mc, Decimal("14"))
        self.assertEqual(
            WalletTransaction.objects.filter(
                wallet=wallet, kind="hidden_listing_fee"
            ).count(),
            1,
        )

    def test_hidden_listing_requires_one_free_mc(self):
        wallet = self.verify_seller(balance="0.5")
        payload = dict(self.payload)
        payload["status"] = "hidden"
        payload["variants"] = [
            {"sku": "HIDDEN-1", "price_mc": "0.000001", "stock": 1, "attributes": []}
        ]

        response = api_post(self.api_client, "/api/products/mine", payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("коштує 1 MC", str(response.data))
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())
        wallet.refresh_from_db()
        self.assertEqual(wallet.free_balance_mc, Decimal("0.5"))
        self.assertFalse(
            WalletTransaction.objects.filter(wallet=wallet, kind="hidden_listing_fee").exists()
        )

    def test_hidden_listing_fee_rolls_back_if_deposit_cannot_be_locked(self):
        wallet = self.verify_seller(balance="1")
        payload = dict(self.payload)
        payload["status"] = "hidden"

        response = api_post(self.api_client, "/api/products/mine", payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())
        wallet.refresh_from_db()
        self.assertEqual(wallet.free_balance_mc, Decimal("1"))
        self.assertEqual(wallet.deposit_locked_mc, Decimal("0"))
        self.assertFalse(
            WalletTransaction.objects.filter(wallet=wallet, kind="hidden_listing_fee").exists()
        )

    def test_inactive_product_requires_no_deposit(self):
        wallet = self.verify_seller()
        payload = dict(self.payload)
        payload["status"] = "inactive"

        response = api_post(self.api_client, "/api/products/mine", payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("0"))

    def test_switching_active_to_inactive_returns_deposit_frozen(self):
        wallet = self.verify_seller()
        created = api_post(self.api_client, "/api/products/mine", self.payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("5"))

        response = api_patch(
            self.api_client,
            f"/api/products/mine/{api_response_data(created)['id']}",
            {"status": "inactive"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("0"))
        self.assertEqual(wallet.frozen_mc, Decimal("5"))
        self.assertEqual(wallet.free_balance_mc, Decimal("15"))
        self.assertEqual(FrozenPayout.objects.filter(seller=self.seller).count(), 1)

    def test_pausing_sale_freezes_deposit_for_two_weeks(self):
        wallet = self.verify_seller()
        created = api_post(self.api_client, "/api/products/mine", self.payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        wallet.refresh_from_db()
        total_before = wallet.total_mc
        self.assertEqual(total_before, Decimal("20"))

        api_patch(
            self.api_client,
            f"/api/products/mine/{api_response_data(created)['id']}",
            {"status": "inactive"},
            format="json",
        )

        wallet.refresh_from_db()
        payout = FrozenPayout.objects.get(seller=self.seller)
        self.assertEqual(wallet.frozen_mc, Decimal("5"))
        self.assertEqual(wallet.total_mc, total_before)  # загальна сума не змінюється
        self.assertAlmostEqual(
            (payout.release_at - timezone.now()).days, 14, delta=1
        )

    def test_restock_adds_stock_and_locks_additional_deposit(self):
        wallet = self.verify_seller(balance="50")
        created = api_post(
            self.api_client, "/api/products/mine", self.payload, format="json"
        )
        self.assertEqual(created.status_code, 201, created.data)
        product_data = api_response_data(created)
        variant_id = product_data["variants"][0]["id"]

        response = api_post(
            self.api_client,
            f"/api/products/mine/{product_data['id']}/variants/{variant_id}/restock",
            {"quantity": 2},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            ProductVariant.objects.get(pk=variant_id).stock,
            7,
        )
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("7"))
        self.assertEqual(wallet.free_balance_mc, Decimal("43"))
        self.assertEqual(api_response_data(response)["additional_deposit_mc"], "2.000000")

    def test_price_increase_locks_deposit_from_new_price_and_stock(self):
        wallet = self.verify_seller(balance="50")
        created = api_post(
            self.api_client, "/api/products/mine", self.payload, format="json"
        )
        self.assertEqual(created.status_code, 201, created.data)
        product_data = api_response_data(created)
        variant_id = product_data["variants"][0]["id"]

        response = api_post(
            self.api_client,
            f"/api/products/mine/{product_data['id']}/variants/{variant_id}/price",
            {"price_mc": "200"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(ProductVariant.objects.get(pk=variant_id).price_mc, Decimal("200"))
        self.assertTrue(api_response_data(response)["variants"][0]["can_change_price"])
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("10"))
        self.assertEqual(wallet.free_balance_mc, Decimal("40"))
        self.assertEqual(
            api_response_data(response)["additional_deposit_mc"],             "5.000000"
        )
        self.assertEqual(api_response_data(response)["returned_deposit_mc"], "0")

    def test_price_decrease_freezes_excess_deposit_for_fourteen_days(self):
        wallet = self.verify_seller(balance="50")
        created = api_post(
            self.api_client, "/api/products/mine", self.payload, format="json"
        )
        self.assertEqual(created.status_code, 201, created.data)
        product_data = api_response_data(created)
        variant_id = product_data["variants"][0]["id"]

        response = api_post(
            self.api_client,
            f"/api/products/mine/{product_data['id']}/variants/{variant_id}/price",
            {"price_mc": "50"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("2.5"))
        self.assertEqual(wallet.frozen_mc, Decimal("2.5"))
        self.assertEqual(wallet.free_balance_mc, Decimal("45"))
        self.assertEqual(api_response_data(response)["returned_deposit_mc"],         "2.500000")
        payout = FrozenPayout.objects.get(seller=self.seller)
        self.assertEqual(payout.amount_mc, Decimal("2.5"))
        self.assertAlmostEqual(
            (payout.release_at - timezone.now()).days, 14, delta=1
        )

    def test_price_increase_with_insufficient_balance_rolls_back(self):
        wallet = self.verify_seller(balance="20")
        created = api_post(
            self.api_client, "/api/products/mine", self.payload, format="json"
        )
        self.assertEqual(created.status_code, 201, created.data)
        wallet.free_balance_mc = Decimal("4")
        wallet.save(update_fields=["free_balance_mc"])
        product_data = api_response_data(created)
        variant_id = product_data["variants"][0]["id"]

        response = api_post(
            self.api_client,
            f"/api/products/mine/{product_data['id']}/variants/{variant_id}/price",
            {"price_mc": "200"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ProductVariant.objects.get(pk=variant_id).price_mc, Decimal("100"))
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("5"))
        self.assertEqual(wallet.free_balance_mc, Decimal("4"))

    def test_sold_variant_price_cannot_be_changed(self):
        self.verify_seller(balance="50")
        created = api_post(
            self.api_client, "/api/products/mine", self.payload, format="json"
        )
        self.assertEqual(created.status_code, 201, created.data)
        product_data = api_response_data(created)
        variant_id = product_data["variants"][0]["id"]
        buyer = CustomUser.objects.create_user(
            username="price-buyer",
            email="price-buyer@example.com",
            password="test-password",
        )
        order = Order.objects.create(
            buyer=buyer,
            seller=self.seller,
            total_mc=Decimal("100"),
        )
        OrderItem.objects.create(
            order=order,
            variant_id=variant_id,
            product_name="New listing",
            unit_price_mc=Decimal("100"),
            quantity=1,
        )
        products_response = api_get(self.api_client, "/api/products/mine")
        self.assertFalse(
            api_response_data(products_response)["products"][0]["variants"][0][
                "can_change_price"
            ]
        )

        response = api_post(
            self.api_client,
            f"/api/products/mine/{product_data['id']}/variants/{variant_id}/price",
            {"price_mc": "200"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ProductVariant.objects.get(pk=variant_id).price_mc, Decimal("100"))

    def test_restocking_sold_out_product_restores_sales_and_reserves_deposit(self):
        wallet = self.verify_seller(balance="50")
        payload = dict(self.payload)
        payload["status"] = "sold_out"
        payload["variants"] = [
            {"sku": "LISTING-1", "price_mc": "100", "stock": 0, "attributes": []}
        ]
        created = api_post(
            self.api_client, "/api/products/mine", payload, format="json"
        )
        self.assertEqual(created.status_code, 201, created.data)
        product_data = api_response_data(created)
        variant_id = product_data["variants"][0]["id"]

        response = api_post(
            self.api_client,
            f"/api/products/mine/{product_data['id']}/variants/{variant_id}/restock",
            {"quantity": 1},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(api_response_data(response)["status"], "active")
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("1"))
        self.assertEqual(wallet.free_balance_mc, Decimal("49"))

    def test_restock_with_insufficient_balance_rolls_back_stock_and_deposit(self):
        wallet = self.verify_seller(balance="20")
        created = api_post(
            self.api_client, "/api/products/mine", self.payload, format="json"
        )
        self.assertEqual(created.status_code, 201, created.data)
        wallet.free_balance_mc = Decimal("1")
        wallet.save(update_fields=["free_balance_mc"])
        product_data = api_response_data(created)
        variant_id = product_data["variants"][0]["id"]

        response = api_post(
            self.api_client,
            f"/api/products/mine/{product_data['id']}/variants/{variant_id}/restock",
            {"quantity": 2},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ProductVariant.objects.get(pk=variant_id).stock, 5)
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("5"))
        self.assertEqual(wallet.free_balance_mc, Decimal("1"))


class ProductDetailAttributeTests(TestCase):
    """Деталі товару повертають значення характеристик з одиницями виміру."""

    def setUp(self):
        self.seller = CustomUser.objects.create_user(
            username="attribute-seller",
            email="attribute@example.com",
            password="test-password",
        )
        self.category = Category.objects.create(name="Телевізори")
        diagonal = Attribute.objects.create(
            category=self.category, name="Діагональ", kind="number", unit="дюйм", order=1
        )
        self.product = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name="Телевізор",
            product_type="physical",
        )
        variant = ProductVariant.objects.create(
            product=self.product, sku="TV-1", price_mc=Decimal("100"), stock=2
        )
        VariantAttributeValue.objects.create(
            variant=variant, attribute=diagonal, value="55"
        )

    def test_attribute_values_include_unit(self):
        client = cast(APIClient, self.client)
        response = api_get(client, f"/api/products/{self.product.id}")

        self.assertEqual(response.status_code, 200)
        values = api_response_data(response)["variants"][0]["attribute_values"]
        self.assertEqual(values[0]["attribute"], "Діагональ")
        self.assertEqual(values[0]["value"], "55")
        self.assertEqual(values[0]["unit"], "дюйм")

    def test_public_product_detail_includes_seller_avatar_url(self):
        self.seller.avatar = "avatars/seller.png"
        self.seller.save(update_fields=["avatar"])
        client = cast(APIClient, self.client)

        response = api_get(client, f"/api/products/{self.product.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            api_response_data(response)["seller"]["avatar"],
            self.seller.avatar.url,
        )

    def test_hidden_product_link_stops_working_when_product_is_inactive(self):
        self.product.status = "hidden"
        self.product.save()
        token = self.product.hidden_token
        client = cast(APIClient, self.client)

        visible_response = api_get(client, f"/api/products/h/{token}")
        self.assertEqual(visible_response.status_code, 200)

        self.product.status = "inactive"
        self.product.save(update_fields=["status"])
        inactive_response = api_get(client, f"/api/products/h/{token}")
        self.assertEqual(inactive_response.status_code, 404)


class SellerProductSearchTests(TestCase):
    """/api/products/mine?q=... фільтрує лише власні товари за назвою."""

    def setUp(self):
        self.seller = CustomUser.objects.create_user(
            username="search-seller",
            email="search@example.com",
            password="test-password",
        )
        other = CustomUser.objects.create_user(
            username="other-seller",
            email="other@example.com",
            password="test-password",
        )
        category = Category.objects.create(name="Search category")
        Product.objects.create(
            seller=self.seller, category=category, name="Backpack red", product_type="physical"
        )
        Product.objects.create(
            seller=self.seller, category=category, name="Backpack blue", product_type="physical"
        )
        Product.objects.create(
            seller=self.seller, category=category, name="Headphones", product_type="physical"
        )
        Product.objects.create(
            seller=other, category=category, name="Backpack of rival", product_type="physical"
        )
        self.api_client = APIClient()
        self.api_client.force_authenticate(self.seller)

    def product_names(self, query: str | None = None) -> list[str]:
        params = {} if query is None else {"q": query}
        response = api_get(self.api_client, "/api/products/mine", params)
        self.assertEqual(response.status_code, 200)
        return [product["name"] for product in api_response_data(response)["products"]]

    def test_search_is_case_insensitive_and_only_own_products(self):
        names = self.product_names("bAcKpAcK")

        self.assertCountEqual(names, ["Backpack red", "Backpack blue"])
        self.assertNotIn("Backpack of rival", names)

    def test_empty_or_blank_query_returns_all_own_products(self):
        expected = ["Backpack red", "Backpack blue", "Headphones"]

        self.assertCountEqual(self.product_names(""), expected)
        self.assertCountEqual(self.product_names("   "), expected)


class SellerStatusFilterTests(TestCase):
    """?status_filter= обмежує список власних оголошень за статусом."""

    def setUp(self):
        self.seller = CustomUser.objects.create_user(
            username="filter-seller",
            email="filter@example.com",
            password="test-password",
        )
        other = CustomUser.objects.create_user(
            username="filter-other",
            email="filter-other@example.com",
            password="test-password",
        )
        category = Category.objects.create(name="Filter category")
        for name, status in (
            ("Active one", "active"),
            ("Hidden one", "hidden"),
            ("Inactive one", "inactive"),
            ("Sold out one", "sold_out"),
        ):
            Product.objects.create(
                seller=self.seller, category=category, name=name,
                product_type="physical", status=status,
            )
        Product.objects.create(
            seller=other, category=category, name="Rival active",
            product_type="physical", status="active",
        )
        self.api_client = APIClient()
        self.api_client.force_authenticate(self.seller)

    def filtered(self, value: str) -> APIResponse:
        response = api_get(self.api_client, "/api/products/mine", {"status_filter": value})
        self.assertEqual(response.status_code, 200)
        return response

    def names(self, value: str) -> list[str]:
        return [product["name"] for product in api_response_data(self.filtered(value))["products"]]

    def test_active_filter_returns_active_and_hidden(self):
        self.assertCountEqual(self.names("active"), ["Active one", "Hidden one"])

    def test_inactive_filter_returns_inactive_and_sold_out(self):
        self.assertCountEqual(self.names("inactive"), ["Inactive one", "Sold out one"])

    def test_unknown_filter_behaves_like_all(self):
        expected = ["Active one", "Hidden one", "Inactive one", "Sold out one"]

        self.assertCountEqual(self.names("all"), expected)
        self.assertCountEqual(self.names("bogus"), expected)

    def test_filter_never_returns_other_sellers_products(self):
        for value in ("all", "active", "inactive"):
            self.assertNotIn("Rival active", self.names(value))

    def test_filter_does_not_change_categories_field(self):
        data = api_response_data(self.filtered("active"))

        self.assertIsInstance(data["categories"], list)


class ProductReviewListTests(TestCase):
    """GET /api/products/<id>/reviews — список відгуків із медіа."""

    def setUp(self):
        cache.clear()
        self.seller = CustomUser.objects.create_user(
            username="review-seller",
            email="review-seller@example.com",
            password="test-password",
        )
        self.buyer = CustomUser.objects.create_user(
            username="review-buyer",
            email="review-buyer@example.com",
            password="test-password",
        )
        category = Category.objects.create(name="Review category")
        self.product = Product.objects.create(
            seller=self.seller, category=category, name="Reviewed product",
            product_type="physical",
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="REV-1", price_mc=Decimal("10"), stock=5
        )
        author = CustomUser.objects.create_user(
            username="review-author",
            email="review-author@example.com",
            password="test-password",
        )
        review = Review.objects.create(
            product=self.product, author=author, text="Чудовий товар"
        )
        ReviewMedia.objects.create(review=review, file="reviews/photo.png", kind="photo")
        ReviewMedia.objects.create(review=review, file="reviews/clip.mp4", kind="video")
        self.guest = APIClient()

    def reviews_url(self) -> str:
        return f"/api/products/{self.product.id}/reviews"

    def test_list_returns_reviews_with_media_and_author_username(self):
        response = api_get(self.guest, self.reviews_url())

        self.assertEqual(response.status_code, 200)
        data = api_response_data(response)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["reviews"][0]["author"], "review-author")
        self.assertEqual(data["reviews"][0]["text"], "Чудовий товар")
        kinds = [media["kind"] for media in data["reviews"][0]["media"]]
        self.assertCountEqual(kinds, ["photo", "video"])
        self.assertNotIn("review-author@example.com", json.dumps(data))

    def test_guest_sees_reviews_but_cannot_review(self):
        data = api_response_data(api_get(self.guest, self.reviews_url()))

        self.assertEqual(len(data["reviews"]), 1)
        self.assertFalse(data["can_review"])
        self.assertIsNone(data["order_id"])

    def delivered_order(self, buyer: CustomUser) -> Order:
        order = Order.objects.create(
            buyer=buyer, seller=self.seller, status="delivered", total_mc=Decimal("10")
        )
        OrderItem.objects.create(
            order=order, variant=self.variant, product_name="Reviewed product",
            unit_price_mc=Decimal("10"), quantity=1,
        )
        return order

    def test_can_review_only_for_buyer_with_delivered_order_without_review(self):
        order = self.delivered_order(self.buyer)
        buyer_client = APIClient()
        buyer_client.force_authenticate(self.buyer)

        before = api_response_data(api_get(buyer_client, self.reviews_url()))
        self.assertTrue(before["can_review"])
        self.assertEqual(before["order_id"], order.id)

        Review.objects.create(
            product=self.product, author=self.buyer, order=order, text="Мій відгук"
        )
        after = api_response_data(api_get(buyer_client, self.reviews_url()))
        self.assertFalse(after["can_review"])


class CatalogAvailabilityFilterTests(TestCase):
    """/api/products?availability= розподіляє товари за наявністю."""

    def setUp(self):
        seller = CustomUser.objects.create_user(
            username="availability-seller",
            email="availability@example.com",
            password="test-password",
        )
        self.root = Category.objects.create(name="Електроніка")
        self.leaf = Category.objects.create(name="Гаджети", parent=self.root)
        for name, status, stock in (
            ("Active in stock", "active", 5),
            ("Active no stock", "active", 0),
            ("Sold out one", "sold_out", 0),
            ("Hidden one", "hidden", 5),
            ("Inactive one", "inactive", 5),
        ):
            product = Product.objects.create(
                seller=seller, category=self.leaf, name=name,
                product_type="physical", status=status,
            )
            ProductVariant.objects.create(
                product=product, price_mc=Decimal("100"), stock=stock
            )

    def list_data(self, availability: str) -> dict[str, Any]:
        client = cast(APIClient, self.client)
        response = api_get(client, "/api/products", {"availability": availability})
        self.assertEqual(response.status_code, 200)
        return api_response_data(response)

    def names(self, availability: str) -> list[str]:
        return [product["name"] for product in self.list_data(availability)["results"]]

    def test_active_filter_requires_stock(self):
        self.assertCountEqual(self.names("active"), ["Active in stock"])

    def test_sold_out_filter_includes_empty_active(self):
        self.assertCountEqual(
            self.names("sold_out"), ["Active no stock", "Sold out one"]
        )

    def test_all_filter_returns_active_and_sold_out(self):
        self.assertCountEqual(
            self.names("all"), ["Active in stock", "Active no stock", "Sold out one"]
        )

    def test_hidden_and_inactive_never_shown(self):
        for availability in ("all", "active", "sold_out", "bogus"):
            names = self.names(availability)
            self.assertNotIn("Hidden one", names)
            self.assertNotIn("Inactive one", names)

    def test_invalid_value_behaves_like_all(self):
        self.assertCountEqual(self.names("bogus"), self.names("all"))

    def test_count_matches_results(self):
        for availability in ("all", "active", "sold_out"):
            data = self.list_data(availability)
            self.assertEqual(data["count"], len(data["results"]))

    def test_in_stock_flag_reflects_variant_stock(self):
        cards = {card["name"]: card for card in self.list_data("all")["results"]}
        self.assertTrue(cards["Active in stock"]["in_stock"])
        self.assertFalse(cards["Active no stock"]["in_stock"])
        self.assertFalse(cards["Sold out one"]["in_stock"])

    def test_active_filter_returns_only_in_stock_products(self):
        results = self.list_data("active")["results"]
        self.assertTrue(all(card["in_stock"] for card in results))

    def test_category_counters_respect_filter(self):
        client = cast(APIClient, self.client)
        response = api_get(
            client, "/api/catalog/categories", {"availability": "sold_out"}
        )
        self.assertEqual(response.status_code, 200)
        root = next(
            category for category in api_response_data(response)["categories"]
            if category["name"] == "Електроніка"
        )
        self.assertEqual(root["product_count"], 2)


class SellerDigitalFileTests(TestCase):
    """Тип товару визначає категорія; цифрові товари вимагають платних файлів."""

    seller: CustomUser
    api_client: APIClient
    digital_leaf: Category

    def setUp(self):
        cache.clear()
        self.seller = CustomUser.objects.create_user(
            username="digital-seller",
            email="digital@example.com",
            password="test-password",
        )
        SellerVerification.objects.create(
            user=self.seller, age=25, photo="verification/seller.png"
        )
        Wallet.objects.create(user=self.seller, free_balance_mc=Decimal("50"))
        assets = Category.objects.create(name="Ігрові проєкти й ассети")
        self.digital_leaf = Category.objects.create(name="Файлові асети", parent=assets)
        self.api_client = APIClient()
        self.api_client.force_authenticate(self.seller)

    def payload(
        self,
        status: str = "inactive",
        product_type: str = "digital_file",
        variants: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "category_id": self.digital_leaf.id,
            "name": "Цифровий товар",
            "product_type": product_type,
            "status": status,
            "variants": variants or [
                {"sku": "D-1", "price_mc": "100", "stock": 5, "attributes": []}
            ],
        }

    def create(self, **kwargs: Any) -> APIResponse:
        return api_post(
            self.api_client, "/api/products/mine", self.payload(**kwargs), format="json"
        )

    def upload(
        self,
        product_id: int,
        name: str = "guide.pdf",
        is_trial: bool = False,
        variant_id: int | None = None,
    ) -> APIResponse:
        data: dict[str, Any] = {"file": SimpleUploadedFile(name, b"payload")}
        if is_trial:
            data["is_trial"] = "on"
        if variant_id is not None:
            data["variant_id"] = variant_id
        return api_post(
            self.api_client,
            f"/api/products/mine/{product_id}/digital-files",
            data,
            format="multipart",
        )

    def test_product_type_comes_from_category(self):
        response = self.create(product_type="digital_file")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(api_response_data(response)["product_type"], "digital_file")

    def test_foreign_product_type_is_rejected(self):
        response = self.create(product_type="physical")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())

    def test_active_digital_file_without_paid_file_is_rejected(self):
        response = self.create(status="active")

        self.assertEqual(response.status_code, 400)
        self.assertIn("digital_files", api_response_data(response)["fields"])
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())

    def test_inactive_digital_file_without_paid_file_is_allowed(self):
        response = self.create(status="inactive")

        self.assertEqual(response.status_code, 201, response.data)

    def test_patch_to_active_requires_paid_file(self):
        created = api_response_data(self.create(status="inactive"))
        product_id = created["id"]

        response = api_patch(
            self.api_client,
            f"/api/products/mine/{product_id}",
            {"status": "active"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Product.objects.get(pk=product_id).status, "inactive")

    def test_patch_to_active_passes_with_paid_file(self):
        created = api_response_data(self.create(status="inactive"))
        uploaded = self.upload(created["id"], variant_id=created["variants"][0]["id"])
        self.assertEqual(uploaded.status_code, 201, uploaded.data)

        response = api_patch(
            self.api_client,
            f"/api/products/mine/{created['id']}",
            {"status": "active"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Product.objects.get(pk=created["id"]).status, "active")

    def test_trial_file_is_not_counted_as_paid(self):
        created = api_response_data(self.create(status="inactive"))
        uploaded = self.upload(
            created["id"], is_trial=True, variant_id=created["variants"][0]["id"]
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.data)
        self.assertTrue(api_response_data(uploaded)["is_trial"])
        self.assertTrue(
            DigitalFile.objects.get(product_id=created["id"]).is_trial
        )

        response = api_patch(
            self.api_client,
            f"/api/products/mine/{created['id']}",
            {"status": "active"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_every_variant_needs_a_paid_file(self):
        variants = [
            {"sku": "D-1", "price_mc": "100", "stock": 3, "attributes": []},
            {"sku": "D-2", "price_mc": "200", "stock": 3, "attributes": []},
        ]
        created = api_response_data(self.create(status="inactive", variants=variants))
        ordered = created["variants"]

        self.upload(created["id"], variant_id=ordered[0]["id"])
        rejected = api_patch(
            self.api_client,
            f"/api/products/mine/{created['id']}",
            {"status": "active"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)

        self.upload(created["id"], name="second.mp3", variant_id=ordered[1]["id"])
        accepted = api_patch(
            self.api_client,
            f"/api/products/mine/{created['id']}",
            {"status": "active"},
            format="json",
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)

    def test_deleting_last_paid_file_of_active_product_is_rejected(self):
        created = api_response_data(self.create(status="inactive"))
        uploaded = self.upload(created["id"], variant_id=created["variants"][0]["id"])
        file_id = api_response_data(uploaded)["id"]
        api_patch(
            self.api_client,
            f"/api/products/mine/{created['id']}",
            {"status": "active"},
            format="json",
        )

        response = self.api_client.delete(
            f"/api/products/mine/{created['id']}/digital-files?file_id={file_id}"
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(DigitalFile.objects.filter(pk=file_id).exists())

    def test_forbidden_extension_is_rejected(self):
        created = api_response_data(self.create(status="inactive"))

        response = self.upload(created["id"], name="virus.exe")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(DigitalFile.objects.filter(product_id=created["id"]).exists())

    def test_physical_product_does_not_require_files(self):
        electronics = Category.objects.create(name="Електроніка")
        leaf = Category.objects.create(name="Гаджети", parent=electronics)

        response = api_post(
            self.api_client,
            "/api/products/mine",
            {
                "category_id": leaf.id,
                "name": "Фізичний товар",
                "product_type": "physical",
                "status": "active",
                "variants": [
                    {"sku": "P-1", "price_mc": "100", "stock": 5, "attributes": []}
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)


class SellerActivationCodeTests(TestCase):
    seller: CustomUser
    category: Category
    delivery: Attribute
    api_client: APIClient

    def setUp(self):
        self.seller = CustomUser.objects.create_user(
            username="subscription-seller",
            email="subscription@example.com",
            password="test-password",
        )
        SellerVerification.objects.create(
            user=self.seller, age=25, photo="verification/seller.png"
        )
        Wallet.objects.create(user=self.seller, free_balance_mc=Decimal("500"))
        subscriptions = Category.objects.create(name="Підписки")
        games = Category.objects.create(name="Ігрові", parent=subscriptions)
        self.category = Category.objects.create(name="Game Pass", parent=games)
        self.delivery = Attribute.objects.create(
            category=subscriptions,
            name="Видача",
            kind="single",
            required=True,
        )
        AttributeOption.objects.create(
            attribute=self.delivery,
            value="Код активації",
        )
        self.api_client = APIClient()
        self.api_client.force_authenticate(self.seller)

    def create_product(
        self,
        codes: list[str],
        stock: int | None = None,
        delivery: str = "Код активації",
    ) -> APIResponse:
        quantity = len(codes) if stock is None else stock
        return api_post(
            self.api_client,
            "/api/products/mine",
            {
                "category_id": self.category.id,
                "name": "Підписка Game Pass",
                "product_type": "subscription",
                "status": "inactive",
                "variants": [{
                    "price_mc": "10",
                    "stock": quantity,
                    "attributes": [{
                        "attribute_id": self.delivery.id,
                        "value": delivery,
                    }],
                    "activation_codes": codes,
                }],
            },
            format="json",
        )

    def test_codes_are_stored_privately_and_checked_for_global_duplicates(self):
        response = self.create_product(["PASS-001", "PASS-002"])

        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("PASS-001", str(response.data))
        self.assertEqual(
            ActivationCode.objects.filter(
                code_key=activation_code_key("pass-001")
            ).count(),
            1,
        )
        duplicate = self.create_product(["pass-001"])
        check = api_post(
            self.api_client,
            "/api/products/mine/activation-codes/check",
            {"codes": ["PASS-001", "PASS-003"]},
            format="json",
        )

        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(api_response_data(check)["available"], [False, True])
        self.assertEqual(Product.objects.filter(seller=self.seller).count(), 1)

    def test_code_count_must_match_variant_stock_and_local_duplicates_are_rejected(self):
        short = self.create_product(["PASS-001"], stock=2)
        duplicate = self.create_product(["PASS-001", "pass-001"])

        self.assertEqual(short.status_code, 400)
        self.assertEqual(duplicate.status_code, 400)
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())

    def test_removed_account_and_link_delivery_methods_are_rejected(self):
        for delivery in ("Акаунт", "Посилання-активація"):
            with self.subTest(delivery=delivery):
                response = self.create_product([], stock=1, delivery=delivery)
                self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.filter(seller=self.seller).exists())

    def test_restock_adds_activation_codes_for_each_new_unit(self):
        created = self.create_product(["PASS-001"])
        self.assertEqual(created.status_code, 201, created.data)
        product_data = api_response_data(created)
        variant_id = product_data["variants"][0]["id"]
        activated = api_patch(
            self.api_client,
            f"/api/products/mine/{product_data['id']}",
            {"status": "active"},
            format="json",
        )
        self.assertEqual(activated.status_code, 200, activated.data)

        rejected = api_post(
            self.api_client,
            f"/api/products/mine/{product_data['id']}/variants/{variant_id}/restock",
            {"quantity": 2, "activation_codes": ["PASS-002"]},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(ProductVariant.objects.get(pk=variant_id).stock, 1)

        response = api_post(
            self.api_client,
            f"/api/products/mine/{product_data['id']}/variants/{variant_id}/restock",
            {"quantity": 2, "activation_codes": ["PASS-002", "PASS-003"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(ProductVariant.objects.get(pk=variant_id).stock, 3)
        self.assertEqual(
            ActivationCode.objects.filter(variant_id=variant_id).count(),
            3,
        )


class ProductModerationAdminTests(TestCase):
    """Право «products»: блокування та м'яке видалення товарів адмінами."""

    seller: CustomUser
    product: Product
    variant: ProductVariant
    category: Category

    def setUp(self):
        self.seller = CustomUser.objects.create_user(
            username="seller-mod", email="seller-mod@example.com", password="test-password"
        )
        SellerVerification.objects.create(
            user=self.seller, age=25, photo="verification/seller.png"
        )
        Wallet.objects.create(user=self.seller, free_balance_mc=Decimal("100"))
        self.root = Category.objects.create(name="Електроніка")
        self.category = Category.objects.create(name="Гаджети", parent=self.root)
        self.product = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name="Мод товар",
            product_type="physical",
            status="active",
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, price_mc=Decimal("100"), stock=5
        )

        self.main = CustomUser.objects.create_user(
            username="main", email="main@example.com", password="test-password",
            role=CustomUser.ROLE_ADMIN_MAIN,
        )
        self.admin1 = CustomUser.objects.create_user(
            username="adm1", email="adm1@example.com", password="test-password",
            role=CustomUser.ROLE_ADMIN_1,
        )
        self.admin2 = CustomUser.objects.create_user(
            username="adm2", email="adm2@example.com", password="test-password",
            role=CustomUser.ROLE_ADMIN_2,
        )

    def client_for(self, user: CustomUser) -> APIClient:
        client = APIClient()
        client.force_authenticate(user)
        return client

    def grant_products(self, user: CustomUser) -> None:
        AdminRight.objects.create(user=user, right="products")

    def block(self, client: APIClient, product: Product | None = None) -> APIResponse:
        target = product or self.product
        return api_post(
            client,
            f"/api/admin/products/{target.id}/block",
            {"reason": "Порушення правил"},
            format="json",
        )

    def test_admin2_without_right_gets_403(self):
        client = self.client_for(self.admin2)

        self.assertEqual(api_get(client, "/api/admin/products").status_code, 403)
        self.assertEqual(self.block(client).status_code, 403)
        self.assertEqual(
            api_post(client, f"/api/admin/products/{self.product.id}/unblock", {}, format="json").status_code,
            403,
        )
        self.assertEqual(
            api_post(
                client, f"/api/admin/products/{self.product.id}/delete",
                {"reason": "Порушення правил"}, format="json",
            ).status_code,
            403,
        )
        bulk_delete = api_post(
            client,
            f"/api/admin/products/sellers/{self.seller.id}/delete-all",
            {"reason": "Порушення правил"},
            format="json",
        )
        self.assertEqual(bulk_delete.status_code, 403)

    def test_admin2_with_right_soft_deletes_all_seller_products(self):
        self.grant_products(self.admin2)
        blocked = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name="Заблокований товар",
            product_type="physical",
            moderation="blocked",
        )
        already_deleted = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name="Раніше видалений товар",
            product_type="physical",
            moderation="deleted",
            moderation_reason="Стара причина",
        )

        response = api_post(
            self.client_for(self.admin2),
            f"/api/admin/products/sellers/{self.seller.id}/delete-all",
            {"reason": "Порушення правил продавцем"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["deleted_count"], 2)
        self.product.refresh_from_db()
        blocked.refresh_from_db()
        already_deleted.refresh_from_db()
        self.assertEqual(self.product.moderation, "deleted")
        self.assertEqual(blocked.moderation, "deleted")
        self.assertEqual(already_deleted.moderation_reason, "Стара причина")
        self.assertTrue(Product.objects.filter(pk=self.product.id).exists())
        self.assertEqual(
            Notification.objects.filter(user=self.seller, section="products").count(),
            1,
        )
        self.assertTrue(
            AdminLog.objects.filter(
                actor=self.admin2,
                target=self.seller,
                action__contains="Видалив усі товари продавця",
            ).exists()
        )

    def test_admin2_cannot_bulk_delete_main_admin_products(self):
        self.grant_products(self.admin2)
        main_seller = CustomUser.objects.create_user(
            username="main-seller",
            email="main-seller@example.com",
            password="test-password",
            role=CustomUser.ROLE_ADMIN_MAIN,
        )
        main_product = Product.objects.create(
            seller=main_seller,
            category=self.category,
            name="Товар головного адміна",
            product_type="physical",
        )

        response = api_post(
            self.client_for(self.admin2),
            f"/api/admin/products/sellers/{main_seller.id}/delete-all",
            {"reason": "Порушення правил"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        main_product.refresh_from_db()
        self.assertEqual(main_product.moderation, "ok")

    def test_admin2_with_right_blocks_unblocks_and_soft_deletes(self):
        self.grant_products(self.admin2)
        client = self.client_for(self.admin2)

        response = self.block(client)
        self.assertEqual(response.status_code, 200, response.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.moderation, "blocked")
        self.assertEqual(self.product.moderation_reason, "Порушення правил")
        self.assertEqual(self.product.moderated_by_id, self.admin2.id)
        self.assertTrue(
            Notification.objects.filter(user=self.seller, section="products").exists()
        )
        self.assertTrue(
            AdminLog.objects.filter(actor=self.admin2, target=self.seller).exists()
        )

        unblocked = api_post(client, f"/api/admin/products/{self.product.id}/unblock", {}, format="json")
        self.assertEqual(unblocked.status_code, 200, unblocked.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.moderation, "ok")

        deleted = api_post(
            client, f"/api/admin/products/{self.product.id}/delete",
            {"reason": "Заборонений товар"}, format="json",
        )
        self.assertEqual(deleted.status_code, 200, deleted.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.moderation, "deleted")
        # М'яке видалення: рядок лишається в базі
        self.assertTrue(Product.objects.filter(pk=self.product.id).exists())

    def test_admin2_cannot_restore_deleted_but_admin1_can(self):
        self.grant_products(self.admin2)
        client2 = self.client_for(self.admin2)
        api_post(
            client2, f"/api/admin/products/{self.product.id}/delete",
            {"reason": "Порушення правил"}, format="json",
        )

        forbidden = api_post(client2, f"/api/admin/products/{self.product.id}/unblock", {}, format="json")
        self.assertEqual(forbidden.status_code, 403)

        allowed = api_post(
            self.client_for(self.admin1),
            f"/api/admin/products/{self.product.id}/unblock", {}, format="json",
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.moderation, "ok")

    def test_reason_is_required_and_short_reason_rejected(self):
        self.grant_products(self.admin2)
        client = self.client_for(self.admin2)

        self.assertEqual(
            api_post(client, f"/api/admin/products/{self.product.id}/block", {}, format="json").status_code,
            400,
        )
        self.assertEqual(
            api_post(
                client, f"/api/admin/products/{self.product.id}/block",
                {"reason": "abc"}, format="json",
            ).status_code,
            400,
        )
        self.assertEqual(
            api_post(
                client, f"/api/admin/products/{self.product.id}/delete", {}, format="json",
            ).status_code,
            400,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.moderation, "ok")

    def test_blocked_product_is_hidden_everywhere(self):
        self.grant_products(self.admin2)
        self.block(self.client_for(self.admin2))
        guest = APIClient()

        listing = api_response_data(api_get(guest, "/api/products"))
        self.assertNotIn("Мод товар", [card["name"] for card in listing["results"]])

        found = api_response_data(api_get(guest, "/api/products", {"q": "Мод"}))
        self.assertEqual(found["count"], 0)

        categories = api_response_data(api_get(guest, "/api/catalog/categories"))
        root = next(
            item for item in categories["categories"] if item["name"] == "Електроніка"
        )
        self.assertEqual(root["product_count"], 0)

        self.assertEqual(api_get(guest, f"/api/products/{self.product.id}").status_code, 404)

        self.product.hidden_token = "secret-token"
        self.product.save(update_fields=["hidden_token"])
        self.assertEqual(
            api_get(guest, "/api/products/h/secret-token").status_code, 404
        )

    def test_blocked_product_cannot_be_added_to_cart_or_checked_out(self):
        key_category = Category.objects.create(name="Ключі")
        digital = Product.objects.create(
            seller=self.seller, category=key_category, name="Ключ",
            product_type="digital_key", status="active",
        )
        variant = ProductVariant.objects.create(
            product=digital, price_mc=Decimal("50"), stock=3
        )
        self.grant_products(self.admin2)
        self.block(self.client_for(self.admin2), digital)

        buyer = CustomUser.objects.create_user(
            username="mod-buyer", email="mod-buyer@example.com", password="test-password",
        )
        Wallet.objects.create(user=buyer, free_balance_mc=Decimal("1000"))
        client = self.client_for(buyer)

        added = api_post(
            client, "/api/cart/items", {"variant_id": variant.id, "quantity": 1}, format="json"
        )
        self.assertEqual(added.status_code, 400)
        self.assertEqual(added.data["error"], "Товар недоступний.")

        cart, _ = Cart.objects.get_or_create(user=buyer)
        CartItem.objects.create(
            cart=cart, variant=variant, quantity=1, price_at_add=variant.price_mc
        )
        checkout = api_post(client, "/api/checkout", {}, format="json")
        self.assertEqual(checkout.status_code, 400)
        self.assertIn("недоступний", json.dumps(checkout.data, ensure_ascii=False))

    def test_blocked_product_excluded_from_seller_deposit(self):
        recalculate_seller_deposit(self.seller)
        self.assertEqual(seller_deposit_required(self.seller.id), Decimal("5"))
        wallet = Wallet.objects.get(user=self.seller)
        self.assertEqual(wallet.deposit_locked_mc, Decimal("5"))

        self.grant_products(self.admin2)
        self.block(self.client_for(self.admin2))

        self.assertEqual(seller_deposit_required(self.seller.id), Decimal("0"))
        wallet.refresh_from_db()
        self.assertEqual(wallet.deposit_locked_mc, Decimal("0"))
        self.assertEqual(wallet.frozen_mc, Decimal("5"))

    def test_seller_cannot_edit_blocked_product_and_is_notified(self):
        self.grant_products(self.admin2)
        self.block(self.client_for(self.admin2))
        self.assertTrue(
            Notification.objects.filter(
                user=self.seller, section="products", kind="moderation"
            ).exists()
        )

        seller_client = self.client_for(self.seller)
        listing = api_response_data(api_get(seller_client, "/api/products/mine"))
        row = next(
            item for item in listing["products"] if item["id"] == self.product.id
        )
        self.assertEqual(row["moderation"], "blocked")
        self.assertEqual(row["moderation_reason"], "Порушення правил")

        response = api_patch(
            seller_client, f"/api/products/mine/{self.product.id}",
            {"name": "Нова назва"}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "заблоковано",
            json.dumps(api_response_data(response), ensure_ascii=False).lower(),
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Мод товар")

    def test_admin_main_products_only_blocked_by_admin_main(self):
        main_product = Product.objects.create(
            seller=self.main, category=self.category, name="Товар головного",
            product_type="physical", status="active",
        )

        denied = self.block(self.client_for(self.admin1), main_product)
        self.assertEqual(denied.status_code, 403)
        main_product.refresh_from_db()
        self.assertEqual(main_product.moderation, "ok")

        allowed = self.block(self.client_for(self.main), main_product)
        self.assertEqual(allowed.status_code, 200, allowed.data)
        main_product.refresh_from_db()
        self.assertEqual(main_product.moderation, "blocked")

    def test_paid_order_is_not_changed_by_moderation(self):
        buyer = CustomUser.objects.create_user(
            username="paid-buyer", email="paid-buyer@example.com", password="test-password",
        )
        order = Order.objects.create(
            buyer=buyer, seller=self.seller, status="paid", total_mc=Decimal("100")
        )
        OrderItem.objects.create(
            order=order, variant=self.variant, product_name=self.product.name,
            unit_price_mc=Decimal("100"), quantity=1,
        )

        self.grant_products(self.admin2)
        self.block(self.client_for(self.admin2))

        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.get().product_name, "Мод товар")
