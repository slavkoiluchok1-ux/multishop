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
    help = "Ідемпотентно створює початкове дерево категорій і демонстраційні товари."

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

            seed_products = list(PRODUCTS)

            demo_sections = [
                ("Електроніка", ["Електроніка", "Смартфони й зв'язок", "Смартфони"], "physical", "SmartPhone", "Smartphone"),
                ("Комп'ютери", ["Електроніка", "Комп'ютери й ноутбуки", "Ноутбуки", "офісні"], "physical", "OfficeBook", "Ноутбук"),
                ("Аудіо", ["Електроніка", "Аудіо", "Навушники", "бездротові TWS"], "physical", "SoundPro", "Навушники"),
                ("Одяг", ["Одяг, взуття, аксесуари", "Чоловічий одяг", "Футболки й поло"], "physical", "UrbanWear", "Футболка"),
                ("Взуття", ["Одяг, взуття, аксесуари", "Взуття", "Кросівки", "повсякденні"], "physical", "StreetStep", "Кросівки"),
                ("Меблі", ["Дім і меблі", "Меблі", "Вітальня", "дивани"], "physical", "HomeStyle", "Диван"),
                ("Освітлення", ["Дім і меблі", "Освітлення", "Настільні лампи"], "physical", "LightHome", "Лампа"),
                ("Ігри", ["Ігри", "PC", "Steam", "Екшен", "шутери від першого обличчя"], "digital_key", "GameForge", "Гра"),
                ("Софт", ["Софт і ліцензії", "Офісні програми"], "digital_key", "OfficeKey", "Ліцензія"),
                ("Навчання", ["Навчання й контент", "Курси", "Дизайн", "UI/UX"], "digital_file", "SkillUp", "Курс"),
                ("Асет", ["Ігрові проєкти й ассети", "2D-ресурси", "Іконки"], "digital_file", "PixelCraft", "Набір"),
                ("Послуга", ["Послуги", "Дизайн", "Логотипи й айдентика"], "service", "StudioPro", "Послуга"),
                ("Підписка", ["Підписки", "Навчання й продуктивність", "Coursera"], "subscription", "Learning+", "Підписка"),
            ]
            for section_index, (label, path, kind, brand, item_label) in enumerate(demo_sections):
                for item_index in range(1, 17):
                    number = section_index * 16 + item_index
                    seed_products.append((
                        f"{item_label} {label} #{number}",
                        path,
                        kind,
                        str(600 + number * 37),
                        999 if kind in ("digital_key", "digital_file", "subscription") else 20,
                        brand,
                        "active",
                    ))

            created_count = 0
            for index, (name, path, kind, price, stock, brand, status) in enumerate(seed_products):
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
