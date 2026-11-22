from decimal import Decimal
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from apps.accounts.models import CustomUser
from apps.catalog.category_data import TREE
from apps.catalog.models import Category, Product, ProductVariant
from apps.core.management.commands.seed_demo import PRODUCTS
from apps.wallet.models import Wallet


class Command(BaseCommand):
    help = "Створює початкове дерево категорій і тестові товари лише для порожньої БД."

    def create_categories(self, data, parent=None):
        if isinstance(data, dict):
            entries = ((name, subtree, order) for order, (name, subtree) in enumerate(data.items()))
        elif isinstance(data, list):
            entries = ((name, None, order) for order, name in enumerate(data))
        else:
            entries = ()
        for name, subtree, order in entries:
            category, _ = Category.objects.get_or_create(
                parent=parent,
                name=name,
                defaults={"order": order, "is_active": True},
            )
            if not category.is_active:
                category.is_active = True
                category.save(update_fields=["is_active"])
            self.create_categories(subtree, parent=category)

    def get_category(self, path):
        parent = None
        for name in path:
            category = Category.objects.filter(parent=parent, name=name).first()
            if category is None:
                category = Category.objects.create(parent=parent, name=name)
            parent = category
        return parent

    def handle(self, *args, **options):
        if Product.objects.exists():
            self.stdout.write("Товари вже існують; початковий seed пропущено.")
            return

        with transaction.atomic():
            self.create_categories(TREE)

            seller, _ = CustomUser.objects.get_or_create(
                username="bootstrap_seller",
                defaults={
                    "email": "bootstrap-seller@bootstrap.local",
                },
            )
            if not seller.has_usable_password():
                seller.set_unusable_password()
                seller.save(update_fields=["password"])
            Wallet.objects.get_or_create(user=seller)

            created_count = 0
            for index, (name, path, kind, price, stock, brand, status) in enumerate(PRODUCTS):
                category = self.get_category(path)
                product, created = Product.objects.update_or_create(
                    seller=seller,
                    name=name,
                    defaults={
                        "category": category,
                        "product_type": kind,
                        "status": status,
                        "brand": brand,
                        "description": f"{name}. Початковий демонстраційний товар.",
                    },
                )
                if created:
                    created_count += 1

                if not product.main_image:
                    photo_name = slugify(name, allow_unicode=True) + ".png"
                    photo_path = Path(__file__).resolve().parents[4] / "seed" / "products" / photo_name
                    if photo_path.is_file():
                        with photo_path.open("rb") as photo_file:
                            product.main_image.save(photo_name, File(photo_file), save=True)

                ProductVariant.objects.update_or_create(
                    product=product,
                    sku=f"BOOT-{index + 1:04d}",
                    defaults={
                        "price_mc": Decimal(price),
                        "stock": stock,
                    },
                )

        self.stdout.write(
            self.style.SUCCESS(f"Початкові товари готові: створено {created_count}.")
        )
