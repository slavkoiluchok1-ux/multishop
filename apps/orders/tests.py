from decimal import Decimal
from typing import Any, cast

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.response import Response as APIResponse
from rest_framework.test import APIClient

from apps.accounts.models import AdminRight, CustomUser, SellerVerification
from apps.cart.models import Cart, CartItem
from apps.catalog.models import (
    Attribute,
    Category,
    DigitalFile,
    Product,
    ProductVariant,
    VariantAttributeValue,
)
from apps.notifications.models import Notification
from apps.orders.models import (
    ActivationCode,
    Order,
    OrderCancellation,
    OrderComment,
    OrderItem,
)
from apps.wallet.models import EscrowHold, FrozenPayout, SellerDeposit, Wallet


def api_get(client: APIClient, *args: Any, **kwargs: Any) -> APIResponse:
    return cast(APIResponse, client.get(*args, **kwargs))


def api_post(client: APIClient, *args: Any, **kwargs: Any) -> APIResponse:
    return cast(APIResponse, client.post(*args, **kwargs))


def api_response_data(response: APIResponse) -> dict[str, Any]:
    return cast(dict[str, Any], response.data)


class OrderMoneyFlowTests(TestCase):
    buyer: CustomUser
    seller: CustomUser
    product: Product
    variant: ProductVariant
    buyer_wallet: Wallet
    seller_wallet: Wallet
    buyer_client: APIClient
    seller_client: APIClient
    checkout_data: dict[str, Any]

    def setUp(self):
        self.buyer = CustomUser.objects.create_user(
            username="buyer", email="buyer@example.com", password="test-password"
        )
        self.seller = CustomUser.objects.create_user(
            username="seller", email="seller@example.com", password="test-password"
        )
        SellerVerification.objects.create(
            user=self.seller,
            age=25,
            photo="verification/seller.png",
        )
        category = Category.objects.create(name="Test category")
        self.product = Product.objects.create(
            seller=self.seller,
            category=category,
            name="Test product",
            product_type="physical",
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="TEST-1",
            price_mc=Decimal("100"),
            stock=5,
        )

        self.buyer_wallet = Wallet.objects.create(
            user=self.buyer,
            free_balance_mc=Decimal("1000"),
        )
        self.seller_wallet = Wallet.objects.create(
            user=self.seller,
            free_balance_mc=Decimal("20"),
            deposit_locked_mc=Decimal("15"),
        )
        cart = Cart.objects.create(user=self.buyer)
        CartItem.objects.create(
            cart=cart,
            variant=self.variant,
            quantity=1,
            price_at_add=Decimal("100"),
        )

        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(self.buyer)
        self.seller_client = APIClient()
        self.seller_client.force_authenticate(self.seller)
        self.checkout_data = {
            "contact_email": "buyer@example.com",
            "contact_phone": "+380001112233",
            "delivery_address": {
                "country": "Україна",
                "city": "Київ",
                "street": "Тестова, 1",
            },
        }

    def tearDown(self):
        for file_record in DigitalFile.objects.filter(product=self.product):
            if file_record.file.name:
                file_record.file.storage.delete(file_record.file.name)

    def checkout(self):
        response = api_post(
            self.buyer_client,
            "/api/checkout", self.checkout_data, format="json"
        )
        data = api_response_data(response)
        self.assertEqual(response.status_code, 201, data)
        return data["orders"][0]

    def make_code_subscription(self, codes: list[str], quantity: int):
        subscriptions = Category.objects.create(name="Підписки")
        games = Category.objects.create(name="Ігрові", parent=subscriptions)
        game_pass = Category.objects.create(name="Game Pass", parent=games)
        self.product.category = game_pass
        self.product.product_type = "subscription"
        self.product.save(update_fields=["category", "product_type"])
        delivery = Attribute.objects.create(
            category=subscriptions,
            name="Видача",
            kind="single",
            required=True,
        )
        VariantAttributeValue.objects.create(
            variant=self.variant,
            attribute=delivery,
            value="Код активації",
        )
        self.variant.stock = quantity
        self.variant.save(update_fields=["stock"])
        CartItem.objects.filter(variant=self.variant).update(quantity=quantity)
        for code in codes:
            ActivationCode.objects.create(variant=self.variant, code=code)

    def test_checkout_holds_payment_and_reduces_stock_atomically(self):
        payload = self.checkout()

        self.buyer_wallet.refresh_from_db()
        self.seller_wallet.refresh_from_db()
        self.variant.refresh_from_db()
        order = Order.objects.get(buyer=self.buyer)

        self.assertEqual(payload["status"], "paid")
        self.assertEqual(order.total_mc, Decimal("100"))
        self.assertEqual(order.fee_mc, Decimal("3"))
        self.assertEqual(self.buyer_wallet.free_balance_mc, Decimal("900"))
        self.assertEqual(self.buyer_wallet.held_mc, Decimal("100"))
        self.assertEqual(self.seller_wallet.deposit_locked_mc, Decimal("12"))
        self.assertEqual(self.seller_wallet.frozen_mc, Decimal("3"))
        self.assertEqual(self.variant.stock, 4)
        self.assertEqual(EscrowHold.objects.get(order=order).status, "held")
        self.assertEqual(CartItem.objects.filter(cart__user=self.buyer).count(), 0)

    def test_subscription_codes_are_delivered_per_unit_only_to_the_buyer(self):
        self.make_code_subscription(["PASS-001", "PASS-002"], quantity=2)

        payload = self.checkout()
        buyer_detail = api_response_data(
            api_get(self.buyer_client, f"/api/orders/{payload['id']}")
        )
        seller_detail = api_response_data(
            api_get(self.seller_client, f"/api/orders/{payload['id']}")
        )

        self.assertEqual(payload["status"], "delivered")
        self.assertEqual(
            payload["items"][0]["activation_codes"],
            ["PASS-001", "PASS-002"],
        )
        self.assertEqual(
            buyer_detail["items"][0]["activation_codes"],
            ["PASS-001", "PASS-002"],
        )
        self.assertEqual(seller_detail["items"][0]["activation_codes"], [])
        self.assertEqual(
            ActivationCode.objects.filter(
                variant=self.variant,
                order_item=OrderItem.objects.get(order__buyer=self.buyer),
            ).count(),
            2,
        )

    def test_checkout_without_enough_activation_codes_changes_nothing(self):
        self.make_code_subscription(["PASS-001"], quantity=2)
        self.buyer_wallet.refresh_from_db()
        initial_balance = self.buyer_wallet.free_balance_mc

        response = api_post(
            self.buyer_client,
            "/api/checkout",
            self.checkout_data,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.filter(buyer=self.buyer).exists())
        self.assertEqual(
            ActivationCode.objects.filter(order_item__isnull=False).count(),
            0,
        )
        self.assertEqual(CartItem.objects.filter(variant=self.variant).get().quantity, 2)
        self.buyer_wallet.refresh_from_db()
        self.assertEqual(self.buyer_wallet.free_balance_mc, initial_balance)

    def test_checkout_saves_named_contacts_and_initial_order_comment(self):
        data = dict(self.checkout_data)
        data["contact_other"] = "Instagram: @buyer\nViber: +380000000000"
        data["order_comment"] = "Будь ласка, напишіть перед відправленням."
        response = api_post(self.buyer_client, "/api/checkout", data, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(buyer=self.buyer)
        self.assertEqual(order.contact_other, data["contact_other"])
        comment = order.comments.get()
        self.assertEqual(comment.text, data["order_comment"])
        self.assertEqual(comment.author, self.buyer)
        self.assertTrue(
            Notification.objects.filter(
                user=self.seller,
                kind="order_comment",
                payload__order_id=order.id,
            ).exists()
        )

    def test_checkout_with_insufficient_balance_changes_nothing(self):
        self.buyer_wallet.free_balance_mc = Decimal("99")
        self.buyer_wallet.save(update_fields=["free_balance_mc"])

        response = api_post(
            self.buyer_client,
            "/api/checkout", self.checkout_data, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.filter(buyer=self.buyer).exists())
        self.assertEqual(CartItem.objects.filter(cart__user=self.buyer).count(), 1)
        self.variant.refresh_from_db()
        self.buyer_wallet.refresh_from_db()
        self.assertEqual(self.variant.stock, 5)
        self.assertEqual(self.buyer_wallet.free_balance_mc, Decimal("99"))
        self.assertEqual(self.buyer_wallet.held_mc, Decimal("0"))

    def test_hidden_product_is_purchased_and_stays_hidden_when_sold_out(self):
        self.variant.stock = 1
        self.variant.save(update_fields=["stock"])
        self.product.status = "hidden"
        self.product.save(update_fields=["status"])

        payload = self.checkout()

        self.product.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(payload["status"], "paid")
        self.assertEqual(self.variant.stock, 0)
        self.assertEqual(self.product.status, "hidden")  # не стає sold_out

    def test_inactive_product_cannot_be_purchased(self):
        self.product.status = "inactive"
        self.product.save(update_fields=["status"])

        response = api_post(
            self.buyer_client,
            "/api/checkout", self.checkout_data, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.filter(buyer=self.buyer).exists())

    def test_confirm_received_moves_escrow_to_frozen_payout(self):
        payload = self.checkout()
        order_id = payload["id"]

        response = api_post(
            self.buyer_client,
            f"/api/orders/{order_id}/confirm-received"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.buyer_wallet.refresh_from_db()
        self.seller_wallet.refresh_from_db()
        order = Order.objects.get(buyer=self.buyer)
        hold = EscrowHold.objects.get(order=order)
        payout = FrozenPayout.objects.get(
            order=order,
            status="frozen",
            amount_mc=Decimal("97"),
        )

        self.assertEqual(order.status, "delivered")
        self.assertIsNotNone(order.received_at)
        self.assertEqual(hold.status, "released")
        self.assertEqual(payout.amount_mc, Decimal("97"))
        self.assertEqual(self.buyer_wallet.held_mc, Decimal("0"))
        self.assertEqual(self.seller_wallet.frozen_mc, Decimal("100"))

    def test_mutual_cancellation_refunds_buyer_and_restores_stock(self):
        payload = self.checkout()
        order_id = payload["id"]

        first_response = api_post(
            self.buyer_client,
            f"/api/orders/{order_id}/cancel-agree"
        )
        first_data = api_response_data(first_response)
        self.assertEqual(first_response.status_code, 200, first_data)
        self.assertFalse(first_data["cancellation"]["seller_agreed"])

        response = api_post(
            self.seller_client,
            f"/api/orders/{order_id}/cancel-agree"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.buyer_wallet.refresh_from_db()
        self.seller_wallet.refresh_from_db()
        self.variant.refresh_from_db()
        order = Order.objects.get(buyer=self.buyer)

        self.assertEqual(order.status, "cancelled")
        self.assertEqual(OrderCancellation.objects.get(order=order).status, "completed")
        self.assertEqual(self.buyer_wallet.free_balance_mc, Decimal("1000"))
        self.assertEqual(self.buyer_wallet.held_mc, Decimal("0"))
        self.assertEqual(self.variant.stock, 5)
        self.assertEqual(EscrowHold.objects.get(order=order).status, "refunded")
        refund = order.refunds.get()
        self.assertEqual(refund.amount_mc, Decimal("100"))
        self.assertEqual(refund.fee_returned_mc, Decimal("0"))

    def test_mutual_cancellation_can_be_rejected_and_withdrawn(self):
        order_id = self.checkout()["id"]

        proposed = api_post(
            self.buyer_client,
            f"/api/orders/{order_id}/cancel-agree",
            {"action": "propose"},
            format="json",
        )
        self.assertEqual(proposed.status_code, 200, proposed.data)
        self.assertEqual(api_response_data(proposed)["cancellation"]["status"], "pending")
        self.assertTrue(
            Notification.objects.filter(user=self.seller, text__contains="Запропоновано").exists()
        )

        rejected = api_post(
            self.seller_client,
            f"/api/orders/{order_id}/cancel-agree",
            {"action": "reject"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 200, rejected.data)
        self.assertEqual(api_response_data(rejected)["cancellation"]["status"], "rejected")
        self.assertEqual(Order.objects.get(pk=order_id).status, "paid")
        self.assertTrue(
            Notification.objects.filter(user=self.buyer, text__contains="відхилено").exists()
        )

        api_post(
            self.buyer_client,
            f"/api/orders/{order_id}/cancel-agree",
            {"action": "propose"},
            format="json",
        )
        withdrawn = api_post(
            self.buyer_client,
            f"/api/orders/{order_id}/cancel-agree",
            {"action": "withdraw"},
            format="json",
        )

        self.assertEqual(withdrawn.status_code, 200, withdrawn.data)
        self.assertEqual(api_response_data(withdrawn)["cancellation"]["status"], "withdrawn")
        self.assertEqual(Order.objects.get(pk=order_id).status, "paid")
        self.assertTrue(
            Notification.objects.filter(user=self.seller, text__contains="відкликано").exists()
        )

    def test_order_comments_are_visible_to_participants_and_increment_counter(self):
        order_id = self.checkout()["id"]
        Notification.objects.filter(user=self.seller).delete()

        response = api_post(
            self.buyer_client,
            f"/api/orders/{order_id}/comments",
            {"text": "Коли буде відправлення?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(OrderComment.objects.get(order_id=order_id).text, "Коли буде відправлення?")
        details = api_get(self.seller_client, f"/api/orders/{order_id}")
        self.assertEqual(api_response_data(details)["comments"][0]["text"], "Коли буде відправлення?")
        counters = api_get(self.seller_client, "/api/notifications/counters")
        self.assertEqual(api_response_data(counters)["counters"]["orders"], 1)

        outsider = CustomUser.objects.create_user(
            username="outsider", email="outsider@example.com", password="test-password"
        )
        outsider_client = APIClient()
        outsider_client.force_authenticate(outsider)
        forbidden = api_post(
            outsider_client,
            f"/api/orders/{order_id}/comments",
            {"text": "Чужий коментар"},
            format="json",
        )
        self.assertEqual(forbidden.status_code, 404)

    def drain_seller_deposit(self):
        """Прибирає заблоковану заставу і вільні MC у продавця."""
        self.seller_wallet.deposit_locked_mc = Decimal("0")
        self.seller_wallet.free_balance_mc = Decimal("0")
        self.seller_wallet.save(
            update_fields=["deposit_locked_mc", "free_balance_mc"]
        )

    def test_checkout_succeeds_without_seller_deposit_funds(self):
        self.drain_seller_deposit()

        payload = self.checkout()

        self.seller_wallet.refresh_from_db()
        deposit = SellerDeposit.objects.get(seller=self.seller)
        self.assertEqual(payload["status"], "paid")
        # Застава не збільшується, нічого не заморожено, покупка не падає
        self.assertEqual(self.seller_wallet.deposit_locked_mc, Decimal("0"))
        self.assertEqual(self.seller_wallet.frozen_mc, Decimal("0"))
        self.assertEqual(deposit.required_mc, Decimal("12"))

    def test_confirm_received_does_not_fail_without_deposit_funds(self):
        self.drain_seller_deposit()
        payload = self.checkout()

        response = api_post(
            self.buyer_client,
            f"/api/orders/{payload['id']}/confirm-received",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.seller_wallet.refresh_from_db()
        self.assertEqual(self.seller_wallet.deposit_locked_mc, Decimal("0"))

    def test_mutual_cancellation_does_not_fail_without_deposit_funds(self):
        self.drain_seller_deposit()
        payload = self.checkout()
        order_id = payload["id"]

        api_post(self.buyer_client, f"/api/orders/{order_id}/cancel-agree")
        response = api_post(
            self.seller_client, f"/api/orders/{order_id}/cancel-agree"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.seller_wallet.refresh_from_db()
        order = Order.objects.get(buyer=self.buyer)
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(self.seller_wallet.deposit_locked_mc, Decimal("0"))

    def test_paid_digital_file_is_granted_only_to_buyer(self):
        self.product.product_type = "digital_file"
        self.product.save(update_fields=["product_type"])
        file_record = DigitalFile.objects.create(
            product=self.product,
            file=SimpleUploadedFile("paid.pdf", b"private digital content"),
            is_trial=False,
            title="Purchased file",
        )

        payload = self.checkout()
        order = Order.objects.get(buyer=self.buyer)
        self.assertEqual(order.status, "delivered")
        self.assertEqual(payload["items"][0]["downloads"][0]["id"], file_record.id)

        buyer_response = api_get(self.buyer_client, f"/api/downloads/{file_record.id}")
        seller_response = api_get(self.seller_client, f"/api/downloads/{file_record.id}")

        self.assertEqual(buyer_response.status_code, 200)
        self.assertEqual(seller_response.status_code, 404)
        buyer_response.close()
        seller_response.close()

    def test_one_time_development_delivers_preview_and_paid_file(self):
        services = Category.objects.create(name="Послуги")
        development = Category.objects.create(name="Розробка", parent=services)
        web = Category.objects.create(name="Веб", parent=development)
        self.product.category = web
        self.product.product_type = "service"
        self.product.save(update_fields=["category", "product_type"])
        self.variant.stock = 1
        self.variant.save(update_fields=["stock"])
        preview = DigitalFile.objects.create(
            product=self.product,
            file=SimpleUploadedFile("preview.zip", b"preview content"),
            is_trial=True,
            title="Демо",
        )
        paid_file = DigitalFile.objects.create(
            product=self.product,
            file=SimpleUploadedFile("source.zip", b"source code"),
            is_trial=False,
            title="Файли проєкту",
        )

        payload = self.checkout()
        order = Order.objects.get(buyer=self.buyer)
        self.variant.refresh_from_db()
        guest_client = APIClient()
        preview_response = api_get(
            guest_client,
            f"/api/products/{self.product.id}/trial-files/{preview.id}",
        )
        download_response = api_get(
            self.buyer_client,
            f"/api/downloads/{paid_file.id}",
        )

        self.assertEqual(order.status, "delivered")
        self.assertEqual(self.variant.stock, 0)
        self.assertEqual(payload["items"][0]["downloads"][0]["id"], paid_file.id)
        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(download_response.status_code, 200)
        preview_response.close()
        download_response.close()

        self.variant.stock = 1
        self.variant.save(update_fields=["stock"])
        self.product.refresh_from_db(fields=["status"])
        self.product.status = "active"
        self.product.save(update_fields=["status"])
        CartItem.objects.create(
            cart=Cart.objects.get(user=self.buyer),
            variant=self.variant,
            quantity=1,
            price_at_add=Decimal("100"),
        )
        second_checkout = api_post(
            self.buyer_client,
            "/api/checkout",
            self.checkout_data,
            format="json",
        )
        self.assertEqual(second_checkout.status_code, 400)
        self.assertEqual(Order.objects.filter(buyer=self.buyer).count(), 1)

    def test_development_with_multiple_units_cannot_be_purchased(self):
        services = Category.objects.create(name="Послуги")
        development = Category.objects.create(name="Розробка", parent=services)
        web = Category.objects.create(name="Веб", parent=development)
        self.product.category = web
        self.product.product_type = "service"
        self.product.save(update_fields=["category", "product_type"])
        self.variant.stock = 2
        self.variant.save(update_fields=["stock"])
        DigitalFile.objects.create(
            product=self.product,
            file=SimpleUploadedFile("source.zip", b"source code"),
            is_trial=False,
        )

        response = api_post(
            self.buyer_client,
            "/api/checkout",
            self.checkout_data,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.filter(buyer=self.buyer).exists())
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 2)

    def test_trial_file_is_public_but_scoped_to_product(self):
        self.product.product_type = "digital_file"
        self.product.save(update_fields=["product_type"])
        file_record = DigitalFile.objects.create(
            product=self.product,
            file=SimpleUploadedFile("trial.pdf", b"trial content"),
            is_trial=True,
        )
        guest_client = APIClient()

        response = api_get(
            guest_client,
            f"/api/products/{self.product.id}/trial-files/{file_record.id}"
        )
        wrong_product = api_get(
            guest_client,
            f"/api/products/99999/trial-files/{file_record.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(wrong_product.status_code, 404)
        response.close()
        wrong_product.close()

    def test_review_is_allowed_once_after_order_delivery(self):
        payload = self.checkout()
        order_id = payload["id"]
        delivered = api_post(
            self.buyer_client,
            f"/api/orders/{order_id}/confirm-received"
        )
        self.assertEqual(delivered.status_code, 200, delivered.data)

        response = api_post(
            self.buyer_client,
            f"/api/products/{self.product.id}/reviews",
            {"order_id": order_id, "text": "Все добре."},
            format="json",
        )
        duplicate = api_post(
            self.buyer_client,
            f"/api/products/{self.product.id}/reviews",
            {"order_id": order_id, "text": "Ще один відгук."},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(duplicate.status_code, 400)


class OrderDetailAndListViewTests(TestCase):
    """GET /api/orders/{id} і короткий список замовлень."""

    buyer: CustomUser
    seller: CustomUser
    buyer_client: APIClient
    seller_client: APIClient
    digital_files: list[DigitalFile]

    def setUp(self):
        self.buyer = CustomUser.objects.create_user(
            username="buyer", email="buyer@example.com", password="test-password"
        )
        self.seller = CustomUser.objects.create_user(
            username="seller", email="seller@example.com", password="test-password"
        )
        SellerVerification.objects.create(
            user=self.seller, age=25, photo="verification/seller.png"
        )
        Wallet.objects.create(user=self.buyer, free_balance_mc=Decimal("1000"))
        Wallet.objects.create(
            user=self.seller,
            free_balance_mc=Decimal("20"),
            deposit_locked_mc=Decimal("15"),
        )
        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(self.buyer)
        self.seller_client = APIClient()
        self.seller_client.force_authenticate(self.seller)
        self.digital_files = []

    def tearDown(self):
        for record in self.digital_files:
            if record.file.name:
                record.file.storage.delete(record.file.name)

    def make_product(self, name, product_type, price="100", stock=5):
        category = Category.objects.create(name=f"{name} category")
        product = Product.objects.create(
            seller=self.seller,
            category=category,
            name=name,
            product_type=product_type,
        )
        variant = ProductVariant.objects.create(
            product=product, price_mc=Decimal(price), stock=stock
        )
        return product, variant

    def add_to_cart(self, variant, quantity=1):
        cart, _ = Cart.objects.get_or_create(user=self.buyer)
        CartItem.objects.create(
            cart=cart, variant=variant, quantity=quantity, price_at_add=variant.price_mc
        )

    def checkout(self, data):
        response = api_post(self.buyer_client, "/api/checkout", data, format="json")
        body = api_response_data(response)
        self.assertEqual(response.status_code, 201, body)
        return body["orders"][0]

    def physical_checkout(self):
        product, variant = self.make_product("Фізичний товар", "physical")
        self.add_to_cart(variant)
        return self.checkout(
            {
                "contact_email": "buyer@example.com",
                "contact_phone": "+380001112233",
                "delivery_address": {
                    "country": "Україна",
                    "city": "Київ",
                    "street": "Тестова, 1",
                },
            }
        )

    def digital_checkout(self):
        product, variant = self.make_product("Цифровий товар", "digital_file")
        record = DigitalFile.objects.create(
            product=product,
            file=SimpleUploadedFile("file.pdf", b"digital content"),
            is_trial=False,
            title="Файл",
        )
        self.digital_files.append(record)
        self.add_to_cart(variant)
        return self.checkout(
            {
                "contact_email": "buyer@example.com",
                "contact_phone": "+380001112233",
            }
        )

    def test_detail_is_available_to_buyer_and_seller(self):
        order = self.physical_checkout()

        buyer_response = api_get(self.buyer_client, f"/api/orders/{order['id']}")
        seller_response = api_get(self.seller_client, f"/api/orders/{order['id']}")

        self.assertEqual(buyer_response.status_code, 200, buyer_response.data)
        self.assertEqual(seller_response.status_code, 200, seller_response.data)

    def test_detail_returns_404_for_stranger(self):
        order = self.physical_checkout()
        stranger = CustomUser.objects.create_user(
            username="stranger", email="stranger@example.com", password="test-password"
        )
        client = APIClient()
        client.force_authenticate(stranger)

        response = api_get(client, f"/api/orders/{order['id']}")

        self.assertEqual(response.status_code, 404)

    def test_detail_is_available_to_admin_with_oversight_right(self):
        order = self.physical_checkout()
        admin = CustomUser.objects.create_user(
            username="admin2",
            email="admin2@example.com",
            password="test-password",
            role=CustomUser.ROLE_ADMIN_2,
        )
        AdminRight.objects.create(user=admin, right="complaints")
        client = APIClient()
        client.force_authenticate(admin)

        response = api_get(client, f"/api/orders/{order['id']}")

        self.assertEqual(response.status_code, 200, response.data)

    def test_detail_has_address_for_physical_and_not_for_digital(self):
        physical = api_response_data(
            api_get(self.buyer_client, f"/api/orders/{self.physical_checkout()['id']}")
        )
        digital = api_response_data(
            api_get(self.buyer_client, f"/api/orders/{self.digital_checkout()['id']}")
        )

        self.assertIn("Київ", physical["delivery_address"])
        self.assertEqual(physical["recipient_name"], "buyer")
        self.assertEqual(digital["delivery_address"], "")
        self.assertEqual(digital["recipient_name"], "")

    def test_list_returns_short_cards_without_full_contacts(self):
        self.physical_checkout()

        response = api_get(self.buyer_client, "/api/orders/mine")
        data = api_response_data(response)
        card = data["orders"][0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(card["first_item"]["product_name"], "Фізичний товар")
        self.assertNotIn("buyer_contacts", card)
        self.assertNotIn("items", card)
        # У короткому списку не повинно бути жодних контактів чи адреси.
        self.assertNotIn("buyer@example.com", str(data))
        self.assertNotIn("+380001112233", str(data))

    def test_reviewed_comment_is_returned_in_order_payload(self):
        order = self.physical_checkout()
        order_id = order["id"]
        product_id = order["items"][0]["product_id"]
        confirm = api_post(self.buyer_client, f"/api/orders/{order_id}/confirm-received")
        self.assertEqual(confirm.status_code, 200, confirm.data)

        first = api_post(
            self.buyer_client,
            f"/api/products/{product_id}/reviews",
            {"order_id": order_id, "text": "Мій коментар про покупку"},
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)

        payload = api_response_data(api_get(self.buyer_client, f"/api/orders/{order_id}"))
        self.assertEqual(payload["my_reviews"][0]["product_id"], product_id)
        self.assertEqual(payload["my_reviews"][0]["text"], "Мій коментар про покупку")

        duplicate = api_post(
            self.buyer_client,
            f"/api/products/{product_id}/reviews",
            {"order_id": order_id, "text": "Ще один коментар"},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(
            api_response_data(duplicate)["fields"]["order_id"],
            "Ви можете залишити лише один коментар до цього товару.",
        )
