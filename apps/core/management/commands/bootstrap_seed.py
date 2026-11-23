from decimal import Decimal
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from apps.accounts.models import CustomUser
from apps.catalog.category_data import TREE, uses_digital_delivery, walk
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
            name_pools = {
                "Електроніка": [
                    "Pixel Max 128", "NovaPhone 5G", "SmartOne Pro", "Phone Lite",
                    "Galaxy Mini", "AeroPhone Plus", "Ultra Screen", "Pocket Smart",
                    "TechNova X", "DailyPhone 12", "Fusion Phone", "Edge Mini",
                    "Prime Mobile", "SmartLife S", "NextGen Phone", "Pocket Pro",
                ],
                "Комп'ютери": [
                    "OfficeBook Air", "WorkStation 14", "PowerDesk Pro", "SlimBook X",
                    "Study Laptop", "Creator 16", "Daily Work PC", "Compact Tower",
                    "Business Mate", "UltraPad Studio", "Home Office Mini", "NexBook",
                    "WorkCore", "Travel Laptop", "Precision PC", "SilverDesk",
                ],
                "Аудіо": [
                    "SoundAir Pro", "Studio Beats", "QuietPods", "BassLine Mini",
                    "Wave Buds", "Crystal Audio", "DeepSound TWS", "RoadBeat",
                    "Focus Pods", "NightWave", "ClearTalk", "AirBeat Plus",
                    "Pocket Audio", "StudioFlow", "BassPocket", "CloudSound",
                ],
                "Одяг": [
                    "Urban Black Tee", "Essential Cotton Shirt", "Street Polo",
                    "Classic Everyday Tee", "Minimal White Top", "SportFit Polo",
                    "Weekend Cotton", "Premium Navy Shirt", "City Street Tee",
                    "Original Logo Tee", "Soft Cotton Basic", "Graphic Streetwear",
                    "DailyFit Polo", "Retro Print Tee", "Summer Cotton",
                    "Modern Basic Shirt",
                ],
                "Взуття": [
                    "StreetStep Runner", "Urban Walk", "Daily Comfort",
                    "Cloud Sneakers", "Metro Sport", "Classic White",
                    "Active Flex", "City Runner", "LightStep",
                    "Everyday Trainer", "Road Comfort", "Soft Motion",
                    "Modern Street", "Weekend Sneakers", "Go Walk",
                    "SportLine Mini",
                ],
                "Меблі": [
                    "Nordic 3-Seater", "Comfort Cloud Sofa", "Small Space Sofa",
                    "Modern Living Sofa", "SoftGrey Corner", "Classic Cozy",
                    "Loft Sofa", "Compact Apartment", "Family Relax",
                    "Urban Corner", "Dream Soft Sofa", "Minimal Lounge",
                    "Home Comfort", "Relax Modern", "Premium Velvet",
                    "Studio Sofa",
                ],
                "Освітлення": [
                    "LightHome Desk", "Amber Study Lamp", "Minimal Glow",
                    "WorkLight Pro", "Warm Desk Light", "Focus Table Lamp",
                    "Modern Reading Lamp", "SoftGlow 2", "Compact Office Lamp",
                    "Studio Light", "NightDesk", "LED Paper Lamp",
                    "Silver Reading", "Eco Light", "Smart Desk Glow",
                    "Nordic Table Lamp",
                ],
                "Ігри": [
                    "Shadow Arena", "Sky Guardians", "Dungeon Legacy",
                    "Cyber Strike", "Dragon Frontier", "Battle Kingdom",
                    "Neon Racer", "Wild Survival", "Space Vanguard",
                    "Mystic Quest", "Iron Battalion", "Pixel Odyssey",
                    "Arena Legends", "Lost Empire", "Storm Hunters",
                    "Galaxy Tactics",
                ],
                "Софт": [
                    "Office Pro License", "Document Suite", "Secure Office",
                    "WorkKey 2026", "Business Tools", "Office Lifetime",
                    "Pro Productivity", "Creative License", "Cloud Office",
                    "Home Office Key", "Team Workspace", "Premium Suite",
                    "Essential Office", "FileMaster", "Smart Documents",
                    "Office Standard",
                ],
                "Навчання": [
                    "UI/UX з нуля", "Figma для дизайнерів", "Дизайн мобільних інтерфейсів",
                    "Графічний дизайн PRO", "Brand Identity Start", "Веб-дизайн практикум",
                    "Типографія й композиція", "Adobe у реальних проєктах",
                    "Дизайн-портфоліо", "Prototyping Master", "UX Research",
                    "Адаптивний UI", "Design Systems", "Motion для інтерфейсів",
                    "Ілюстрація у Figma", "Комерційний дизайн",
                ],
                "Асет": [
                    "Fantasy Icon Pack", "Space 2D Kit", "Minimal HUD Icons",
                    "RPG Item Set", "Pixel UI Essentials", "Cyberpunk Buttons",
                    "Fantasy Inventory", "Game Interface Icons", "Colorful Emoji Set",
                    "Board Game Assets", "Medieval Icons", "Retro UI Pack",
                    "Adventure Sprites", "Magic Effects 2D", "Action Icon Bundle",
                    "Platformer Tiles",
                ],
                "Послуга": [
                    "Логотип для стартапу", "Бренд-пакет компанії", "Дизайн соціальних постів",
                    "Розробка лендингу", "Аудит UX сайту", "Дизайн інтернет-магазину",
                    "Презентація продукту", "Ребрендинг логотипа", "Ілюстрації для бренду",
                    "Motion-рекламний ролик", "UI-дизайн додатка", "Фірмовий стиль",
                    "Дизайн банера", "Редизайн головної сторінки", "Прототип MVP",
                    "Пакет бренд-графіки",
                ],
                "Підписка": [
                    "Coursera Plus", "Learning Pass", "Skill Premium",
                    "Study Unlimited", "EduPro Місяць", "CourseBox Plus",
                    "Knowledge Premium", "Career Learning", "Productivity Pass",
                    "Tech Courses VIP", "Design Learning", "Soft Skills Monthly",
                    "Smart Education", "IT Academy Pass", "Creative Studies",
                    "Unlimited Learning",
                ],
            }
            for section_index, (label, path, kind, brand, item_label) in enumerate(demo_sections):
                continue
                for item_index in range(1, 17):
                    number = section_index * 16 + item_index
                    old_name = f"{item_label} {label} #{number}"
                    unique_name = name_pools[label][item_index - 1]
                    old_product = Product.objects.filter(seller=seller, name=old_name).first()
                    if old_product is not None:
                        old_product.name = unique_name
                        old_product.slug = slugify(unique_name, allow_unicode=True)
                        old_product.save(update_fields=["name", "slug"])
                    seed_products.append((
                        unique_name,
                        path,
                        kind,
                        str(600 + number * 37),
                        999 if kind in ("digital_key", "digital_file", "subscription") else 20,
                        brand,
                        "active",
                    ))

            leaf_paths = [path for path, is_leaf in walk(TREE) if is_leaf]
            leaves_by_root = {}
            for path in leaf_paths:
                leaves_by_root.setdefault(path[0], []).append(path)
            selected_leaf_paths = []
            leaf_round = 0
            while len(selected_leaf_paths) < 107:
                added = False
                for root_paths in leaves_by_root.values():
                    if leaf_round < len(root_paths):
                        selected_leaf_paths.append(root_paths[leaf_round])
                        added = True
                        if len(selected_leaf_paths) == 107:
                            break
                if not added:
                    break
                leaf_round += 1
            editions = [
                "Classic", "Pro", "Plus", "Lite", "Max", "Essential", "Elite",
                "Studio", "Mini", "Ultra", "Premium", "Compact",
            ]
            for leaf_index, path in enumerate(selected_leaf_paths):
                root = path[0]
                if root == "Підписки":
                    kind = "subscription"
                    brand = "Learning+" if "Навчання" in path else "MediaPass"
                elif root == "Подарункові картки й поповнення":
                    kind = "digital_key"
                    brand = path[-1]
                elif root in ("Ігри", "Софт і ліцензії"):
                    kind = "digital_key"
                    brand = "Steam" if root == "Ігри" else "Microsoft"
                elif root in ("Навчання й контент", "Цифрові дизайн-ресурси", "Ігрові проєкти й ассети"):
                    kind = "digital_file"
                    brand = "DigitalLab"
                elif root == "Послуги":
                    kind = "service"
                    brand = "Studio"
                else:
                    kind = "physical"
                    brand = "MultiShop"
                leaf_name = path[-1]
                for variant_index in range(2):
                    edition = editions[(leaf_index + variant_index) % len(editions)]
                    unique_name = f"{leaf_name} {edition} #{leaf_index * 2 + variant_index + 1}"
                    price = str(700 + (leaf_index * 2 + variant_index) * 43)
                    stock = 999 if kind in ("digital_key", "digital_file", "subscription") else 20
                    seed_products.append((unique_name, list(path), kind, price, stock, brand, "active"))

            original_names = {name for name, *_ in seed_products}
            Product.objects.filter(seller=seller).exclude(name__in=original_names).delete()

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
                if (
                    product.status in ("active", "hidden")
                    and uses_digital_delivery(product.product_type, category.path)
                ):
                    paid_variant_ids = set(
                        product.digital_files.filter(is_trial=False).values_list(
                            "variant_id", flat=True
                        )
                    )
                    variant_ids = set(product.variants.values_list("id", flat=True))
                    if None not in paid_variant_ids and not variant_ids.issubset(paid_variant_ids):
                        product.status = "inactive"
                        product.hidden_token = ""
                        product.save(update_fields=["status", "hidden_token"])

        self.stdout.write(
            self.style.SUCCESS(f"Початкові товари готові: створено {created_count}.")
        )
