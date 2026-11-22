from decimal import Decimal

from django.db import transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import CustomUser
from apps.catalog.models import Category, Product, ProductVariant, sync_stock_status
from apps.notifications.models import Notification

from .models import Favorite


class ProactiveFavoriteNotificationTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="favorite-user",
            email="favorite@example.com",
            password="test-password-123",
        )
        self.category = Category.objects.create(name="Цифрове", slug="digital")
        self.product = Product.objects.create(
            seller=self.user,
            category=self.category,
            name="Тестовий товар",
            product_type="physical",
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            price_mc=Decimal("10"),
            stock=1,
        )
        self.favorite = Favorite.objects.create(
            user=self.user,
            product=self.product,
            last_price=Decimal("10"),
            last_status="active",
        )

    def test_price_change_is_notified_before_favorites_page_is_opened(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.variant.price_mc = Decimal("12")
            self.variant.save(update_fields=["price_mc"])

        notification = Notification.objects.get(user=self.user, section="favorites")
        self.assertIn("10", notification.text)
        self.assertIn("12", notification.text)

        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get("/api/favorites")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Notification.objects.filter(user=self.user, section="favorites").count(),
            1,
        )

    def test_stock_exhaustion_and_moderation_notify_once(self):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                self.variant.stock = 0
                self.variant.save(update_fields=["stock"])
                sync_stock_status(self.product)

        messages = list(
            Notification.objects.filter(user=self.user, section="favorites").values_list(
                "text", flat=True
            )
        )
        self.assertEqual(len(messages), 1)
        self.assertIn("закінчився", messages[0])

        with self.captureOnCommitCallbacks(execute=True):
            self.product.refresh_from_db()
            self.product.moderation = "blocked"
            self.product.save(update_fields=["moderation"])
        self.assertEqual(
            Notification.objects.filter(user=self.user, section="favorites").count(),
            2,
        )

    def test_variant_removal_notifies_saved_users_proactively(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.variant.delete()

        notification = Notification.objects.get(user=self.user, section="favorites")
        self.assertIn("видалено варіант", notification.text)
