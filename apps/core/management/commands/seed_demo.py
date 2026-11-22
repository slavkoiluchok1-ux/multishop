"""Демонстраційні дані Етапу 1: дерево категорій, товари, користувачі, політика.

Запуск:  uv run python manage.py seed_demo
Команда ідемпотентна: повторний запуск не створює дублікатів.
"""
import textwrap
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import TypeAlias

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageDraw, ImageFont

from apps.accounts.models import CustomUser, SellerVerification
from apps.catalog.models import (
    Attribute,
    AttributeOption,
    Category,
    Product,
    ProductVariant,
    VariantAttributeValue,
)
from apps.catalog.category_data import (
    ATTRIBUTES as TREE_ATTRIBUTES,
    TREE as CATEGORY_TREE,
    TreeNode,
    children as tree_children,
    norm as normalize_category_name,
    validate as validate_category_data,
    validate_product as validate_demo_product,
)
from apps.core.models import SiteBlock, SiteSettings
from apps.policy.models import PolicyVersion
from apps.wallet.models import CoinIssue, SellerDeposit, Wallet, WalletTransaction

# --- Кольори-заглушки для фото (збігаються з токенами фронтенду) ---
PALETTE = ["#c8f04a", "#f3c95b", "#7ea2ff", "#f4f0e8", "#b6f0e0", "#f7b3b8"]

# Товари: (назва, шлях категорії, вид, ціна MC, залишок, бренд, статус)
PRODUCTS = [
    ("Смартфон Google Pixel 9", ["Електроніка", "Смартфони й зв'язок", "Смартфони"], "physical", "32000", 12, "Google", "active"),
    ("Навушники Sony WH-1000XM6", ["Електроніка", "Аудіо", "Навушники", "повнорозмірні"], "physical", "12500", 20, "Sony", "active"),
    ("Ноутбук ASUS ROG Strix G18", ["Електроніка", "Комп'ютери й ноутбуки", "Ноутбуки", "ігрові"], "physical", "89000", 5, "ASUS", "active"),
    ("Відеокарта NVIDIA RTX 5080", ["Електроніка", "Комп'ютери й ноутбуки", "Комплектуючі", "Відеокарти", "NVIDIA"], "physical", "74000", 3, "NVIDIA", "active"),
    ("Робот-пилосос Roborock S8", ["Електроніка", "Побутова техніка", "Прибирання", "Роботи-пилососи", "гібридні"], "physical", "26000", 8, "Roborock", "active"),
    ("Консоль PlayStation 5 Slim", ["Електроніка", "Ігрові пристрої", "Консолі", "PlayStation"], "physical", "21000", 6, "Sony", "active"),
    ("Кросівки Nike Air Zoom Pegasus", [ "Одяг, взуття, аксесуари", "Взуття", "Кросівки", "бігові"], "physical", "4200", 30, "Nike", "active"),
    ("Худі Cyberpunk 2077", ["Одяг, взуття, аксесуари", "Геймерський і фандом-одяг", "Худі й футболки за іграми"], "physical", "1600", 40, "CD Projekt", "active"),
    ("Ігрове крісло DXRacer Master", ["Дім і меблі", "Меблі", "Ігрові", "ігрові крісла"], "physical", "9800", 7, "DXRacer", "active"),
    ("Настільна лампа Xiaomi LED", ["Дім і меблі", "Освітлення", "настільні лампи"], "physical", "900", 50, "Xiaomi", "inactive"),
    ("Шуруповерт Bosch GSR 18V", ["Сад, ремонт, інструменти", "Інструменти", "акумуляторні"], "physical", "6500", 15, "Bosch", "active"),
    ("Гантелі розбірні 2×10 кг", ["Спорт і туризм", "Фітнес", "гантелі"], "physical", "2400", 25, "SportPro", "active"),
    ("Автокрісло Cybex Solution Z", ["Дитячі товари", "автокрісла"], "physical", "8500", 9, "Cybex", "active"),
    ("Кава в зернах Arabica 1 кг", ["Їжа й напої", "Кава й чай"], "physical", "1100", 60, "Lavazza", "active"),
    ("Конструктор LEGO Star Wars", ["Книги, канцелярія, хобі", "Конструктори", "LEGO"], "physical", "5200", 14, "LEGO", "active"),
    ("Корм для собак Royal Canin 12 кг", ["Зоотовари", "Для собак", "корми"], "physical", "3100", 0, "Royal Canin", "sold_out"),
    ("Cyberpunk 2077 (Steam)", ["Ігри", "PC", "Steam", "RPG", "Action-RPG"], "digital_key", "1500", 999, "CD Projekt", "active"),
    ("EA SPORTS FC 26 (Steam)", ["Ігри", "PC", "Steam", "Гонки й спорт", "футбол"], "digital_key", "1800", 999, "EA", "active"),
    ("Unity Asset Pack: Fantasy Characters", ["Ігрові проєкти й ассети", "3D-моделі", "Персонажі", "фентезі"], "digital_file", "900", 999, "IndieForge", "active"),
    ("Курс Python + Django", ["Навчання й контент", "Курси", "Програмування", "Python"], "digital_file", "2000", 999, "CodeSchool", "active"),
    ("Steam Gift Card 500", ["Подарункові картки й поповнення", "Steam"], "digital_key", "5000", 999, "Valve", "active"),
    ("Windows 11 Pro (ключ)", ["Софт і ліцензії", "Операційні системи", "Windows"], "digital_key", "3000", 999, "Microsoft", "active"),
    ("UI Kit для Figma", ["Цифрові дизайн-ресурси", "Шаблони сайтів"], "digital_file", "700", 999, "DesignHub", "hidden"),
    ("Розробка інтернет-магазину на Django", ["Послуги", "Розробка", "Веб", "Django"], "service", "60000", 3, "FreelanceDev", "active"),
    ("Xbox Game Pass Ultimate (1 міс.)", ["Підписки", "Ігрові", "Game Pass"], "subscription", "400", 999, "Microsoft", "active"),
    ("Netflix Premium (1 міс.)", ["Підписки", "Стрімінг і медіа", "Netflix"], "subscription", "450", 999, "Netflix", "active"),
]

# Канонічні дерево й характеристики винесені в apps.catalog.category_data.
TREE = CATEGORY_TREE
ATTRIBUTES = TREE_ATTRIBUTES

# Значення характеристик для конкретних демо-товарів (за назвою товару)
AttributeSeedValue: TypeAlias = str | list[str]
ATTR_VALUES: dict[str, dict[str, AttributeSeedValue]] = {
    "Смартфон Google Pixel 9": {
        "Діагональ": "6.3",
        "ОЗП": "12",
        "Накопичувач": "128",
        "Колір": "Сірий",
        "Стан товару": "Новий",
        "Гарантія": "24",
        "Тип смартфона": ["Сенсорний"],
        "Конструкція": "Звичайний",
    },
    "Ноутбук ASUS ROG Strix G18": {
        "Процесор": "Intel Core i9",
        "ОЗП": "32",
        "Накопичувач": "1000",
        "Діагональ": "18",
        "Роздільність": "2K",
    },
    "Відеокарта NVIDIA RTX 5080": {},
    "Навушники Sony WH-1000XM6": {
        "Тип підключення": "Bluetooth",
        "Шумозаглушення": "так",
        "Автономність": "30",
    },
    "Робот-пилосос Roborock S8": {"Wi-Fi": "так", "Колір": "Білий"},
    "Консоль PlayStation 5 Slim": {"Платформа": "PlayStation"},
    "Кросівки Nike Air Zoom Pegasus": {
        "Розмір": "42",
        "Колір": "Чорний",
        "Матеріал": "Поліестер",
        "Сезон": "Всесезонний",
        "Стать": "Чоловіча",
    },
    "Худі Cyberpunk 2077": {
        "Розмір": "L",
        "Колір": "Чорний",
        "Матеріал": "Бавовна",
        "Сезон": "Демісезон",
    },
    "Cyberpunk 2077 (Steam)": {
        "Платформа": "PC",
        "Регіон ключа": "Україна",
        "Мова": ["Українська", "Англійська"],
        "Вікове обмеження": "18+",
        "Рік релізу": "2020",
        "Режим": ["Одиночна"],
    },
    "EA SPORTS FC 26 (Steam)": {
        "Платформа": "PC",
        "Регіон ключа": "Європа",
        "Мова": ["Українська", "Англійська"],
        "Вікове обмеження": "3+",
        "Рік релізу": "2025",
        "Режим": ["Одиночна", "Мультиплеєр"],
    },
    "Unity Asset Pack: Fantasy Characters": {
        "Кількість полігонів": "12500",
        "Ригінг": "так",
        "Анімації": "так",
        "PBR-текстури": "так",
        "LOD": "ні",
        "Формати": ["FBX", "OBJ"],
        "Рушії": ["Unity", "Unreal Engine"],
        "Сумісність": ["Unity", "Unreal Engine"],
        "Ліцензія": "Комерційна",
    },
    "Курс Python + Django": {},
    "Steam Gift Card 500": {},
    "Windows 11 Pro (ключ)": {},
    "UI Kit для Figma": {},
    "Розробка інтернет-магазину на Django": {
        "Формат": "Проєктна",
        "Терміни": "14",
        "Є портфоліо": "так",
        "Мова": ["Українська", "Англійська"],
    },
    "Xbox Game Pass Ultimate (1 міс.)": {
        "Період": "Місяць",
        "Регіон": "Глобальний",
        "Автопродовження": "так",
    },
    "Netflix Premium (1 міс.)": {
        "Період": "Місяць",
        "Регіон": "Глобальний",
        "Автопродовження": "ні",
    },
}

SITE_BLOCKS = [
    ("hero", "MultiShop — маркетплейс у мульти коінах", "Фізичні, цифрові товари, послуги й підписки в одному місці. Розрахунки в MC: 1 MC = 10 грн.", 1),
    ("how", "Як це працює", "Продавець виставляє товар і заставу. Покупець оплачує з гаманця. Кошти тримаються в ескроу до підтвердження.", 2),
    ("trust", "Безпека розрахунків", "Комісія 3%, виплата продавцю заморожується на 14 днів, застава 1% від загальної вартості товарів продавця.", 3),
]


# --- Шрифт для заглушок: обов'язково з підтримкою кирилиці ---
# Вбудований ImageFont.load_default() не має кириличних гліфів — на заглушці
# замість «Верифікація seller1» були б квадратики. Шукаємо перший наявний TTF.
FONT_CANDIDATES = [
    Path(settings.BASE_DIR) / "seed" / "DejaVuSans.ttf",  # власний шрифт проєкту
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/tahoma.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
]


def load_cyrillic_font(size):
    """TTF-шрифт із кирилицею для підписів на заглушках."""
    for path in FONT_CANDIDATES:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise CommandError(
        "Не знайдено шрифт із кирилицею для заглушок. "
        f"Покладіть DejaVuSans.ttf у {Path(settings.BASE_DIR) / 'seed'}"
    )


def make_placeholder(label, index):
    """Заглушка-зображення товару через Pillow."""
    width, height = 800, 600
    bg = PALETTE[index % len(PALETTE)]
    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, width - 20, height - 20], outline="#101820", width=4)
    font = load_cyrillic_font(42)
    lines = textwrap.wrap(label, width=18)[:4]
    line_h = 52
    y = (height - len(lines) * line_h) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text(((width - (bbox[2] - bbox[0])) / 2, y), line, fill="#101820", font=font)
        y += line_h
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return ContentFile(buffer.getvalue())


class Command(BaseCommand):
    help = "Створює демонстраційні дані MultiShop (Етап 1)."

    # --- Дерево категорій ---
    def create_categories(
        self, data: TreeNode, parent: Category | None = None
    ) -> None:
        siblings = list(Category.objects.filter(parent=parent))
        for order, (name, subtree) in enumerate(tree_children(data).items()):
            node = next(
                (category for category in siblings if normalize_category_name(category.name) == name),
                None,
            )
            if node is None:
                node = Category.objects.create(parent=parent, name=name, order=order)
                siblings.append(node)
            else:
                changed = []
                if node.name != name:
                    node.name = name
                    changed.append("name")
                if node.order != order:
                    node.order = order
                    changed.append("order")
                if not node.is_active:
                    node.is_active = True
                    changed.append("is_active")
                if changed:
                    node.save(update_fields=[*changed, "path"])
            self.create_categories(subtree, parent=node)

    def _canonical_category_paths(self) -> set[tuple[str, ...]]:
        paths: set[tuple[str, ...]] = set()

        def collect(data: TreeNode, parent_path: tuple[str, ...] = ()) -> None:
            for name, subtree in tree_children(data).items():
                path = parent_path + (name.casefold(),)
                paths.add(path)
                collect(subtree, path)

        collect(TREE)
        return paths

    def deactivate_obsolete_categories(self) -> int:
        """Hide obsolete category paths without moving or deleting products."""
        expected_paths = self._canonical_category_paths()
        categories = list(Category.objects.all())
        paths = {
            category.id: tuple(
                normalize_category_name(part).casefold()
                for part in category.path.split(" › ")
                if part
            )
            for category in categories
        }
        root_names = {path[0] for path in expected_paths}
        smartphone_path = tuple(
            normalize_category_name(part).casefold()
            for part in ("Електроніка", "Смартфони й зв'язок", "Смартфони")
        )
        obsolete = [
            category
            for category in categories
            if category.is_active
            and paths[category.id]
            and paths[category.id][0] in root_names
            and paths[category.id] not in expected_paths
        ]
        product_category_ids = set(Product.objects.values_list("category_id", flat=True))
        to_deactivate: set[int] = set()
        preserved = 0

        for category in obsolete:
            path = paths[category.id]
            legacy_smartphone_subtype = (
                len(path) > len(smartphone_path)
                and path[:len(smartphone_path)] == smartphone_path
            )
            descendants = {
                category_id
                for category_id, candidate_path in paths.items()
                if candidate_path[:len(path)] == path
            }
            # Smartphone classifications are filters now, not navigable categories.
            if descendants & product_category_ids and not legacy_smartphone_subtype:
                preserved += 1
                continue
            to_deactivate.update(
                category_id
                for category_id in descendants
                if paths[category_id] not in expected_paths
            )

        if to_deactivate:
            Category.objects.filter(id__in=to_deactivate).update(is_active=False)
        if preserved:
            self.stdout.write(
                self.style.WARNING(
                    f"Застарілі гілки з товарами залишено активними: {preserved}"
                )
            )
        return len(to_deactivate)

    def get_category(self, path: list[str] | tuple[str, ...]) -> Category:
        parent = None
        node: Category | None = None
        for name in path:
            normalized = normalize_category_name(name)
            node = next(
                (
                    category
                    for category in Category.objects.filter(parent=parent)
                    if normalize_category_name(category.name) == normalized
                ),
                None,
            )
            if node is None:
                raise CommandError(f"Категорію не знайдено: {' › '.join(path)}")
            parent = node
        if node is None:
            raise CommandError(f"Шлях категорії порожній: {' › '.join(path)}")
        return node

    # --- Користувачі ---
    def create_user(self, username, email, password, role):
        user, created = CustomUser.objects.get_or_create(
            username=username,
            defaults={"email": email, "role": role},
        )
        if created:
            user.set_password(password)
            user.save()
        return user

    # --- Характеристики ---
    def create_attributes(self) -> dict[int, dict[str, Attribute]]:
        """Створює характеристики за типізованою схемою дерева."""
        attr_by_cat: dict[int, dict[str, Attribute]] = {}
        for order, definition in enumerate(ATTRIBUTES):
            name = definition["name"]
            for path in definition["targets"]:
                category = self.get_category(path)
                attribute, _ = Attribute.objects.update_or_create(
                    category=category,
                    name=name,
                    defaults={
                        "kind": definition["kind"],
                        "unit": definition["unit"],
                        "required": definition["required"],
                        "order": order,
                    },
                )
                option_values = definition["options"]
                existing_options = AttributeOption.objects.filter(attribute=attribute)
                if option_values:
                    existing_options.exclude(value__in=option_values).delete()
                else:
                    existing_options.delete()
                for option in option_values:
                    AttributeOption.objects.get_or_create(attribute=attribute, value=option)
                attr_by_cat.setdefault(category.id, {})[name] = attribute  # pyright: ignore[reportAttributeAccessIssue]
        self.remove_obsolete_empty_attributes()
        return attr_by_cat

    def remove_obsolete_empty_attributes(self) -> int:
        """Remove stale schema rows only when no product value depends on them."""
        expected = {
            (
                tuple(normalize_category_name(part).casefold() for part in path),
                normalize_category_name(definition["name"]).casefold(),
            )
            for definition in ATTRIBUTES
            for path in definition["targets"]
        }
        used_ids = set(
            VariantAttributeValue.objects.values_list("attribute_id", flat=True).distinct()
        )
        stale_ids = []
        preserved = 0
        for attribute in Attribute.objects.select_related("category"):
            path = tuple(
                normalize_category_name(part).casefold()
                for part in attribute.category.path.split(" › ")
                if part
            )
            signature = (path, normalize_category_name(attribute.name).casefold())
            if signature in expected:
                continue
            if attribute.id in used_ids:  # pyright: ignore[reportAttributeAccessIssue]
                preserved += 1
            else:
                stale_ids.append(attribute.id)  # pyright: ignore[reportAttributeAccessIssue]

        if stale_ids:
            Attribute.objects.filter(id__in=stale_ids).delete()
        if preserved:
            self.stdout.write(
                self.style.WARNING(
                    f"Застарілі характеристики зі збереженими значеннями залишено: {preserved}"
                )
            )
        return len(stale_ids)

    def applicable_attributes(
        self, attr_by_cat: dict[int, dict[str, Attribute]], category: Category
    ) -> dict[str, Attribute]:
        """Характеристики, успадковані від вузла вгору по дереву (найближчий вузол перемагає)."""
        result: dict[str, Attribute] = {}
        node: Category | None = category
        while node is not None:
            for name, attribute in attr_by_cat.get(node.id, {}).items():  # pyright: ignore[reportAttributeAccessIssue]
                result.setdefault(name, attribute)
            node = node.parent
        return result

    def set_attribute_values(self, attr_by_cat: dict[int, dict[str, Attribute]]) -> int:
        """Значення характеристик для варіантів демо-товарів."""
        count = 0
        for product in Product.objects.select_related("category"):
            if product.name not in ATTR_VALUES:
                continue
            values = ATTR_VALUES[product.name]
            variant = product.variants.first()  # pyright: ignore[reportAttributeAccessIssue]
            if variant is None:
                continue
            applicable = self.applicable_attributes(attr_by_cat, product.category)
            configured = {
                name: applicable[name]
                for name in values
                if name in applicable
            }
            configured_ids = [
                attribute.id  # pyright: ignore[reportAttributeAccessIssue]
                for attribute in configured.values()
            ]
            existing_values = VariantAttributeValue.objects.filter(variant=variant)
            if configured_ids:
                existing_values.exclude(attribute_id__in=configured_ids).delete()
            else:
                existing_values.delete()
            for name, raw_values in values.items():
                attribute = configured.get(name)
                if attribute is None:
                    continue
                selected_values = list(dict.fromkeys(
                    raw_values if isinstance(raw_values, list) else [raw_values]
                ))
                attribute_values = VariantAttributeValue.objects.filter(
                    variant=variant, attribute=attribute
                )
                if attribute.kind == "multiple":
                    if selected_values:
                        attribute_values.exclude(value__in=selected_values).delete()
                    else:
                        attribute_values.delete()
                    for value in selected_values:
                        VariantAttributeValue.objects.get_or_create(
                            variant=variant, attribute=attribute, value=value
                        )
                    count += len(selected_values)
                elif selected_values:
                    VariantAttributeValue.objects.update_or_create(
                        variant=variant,
                        attribute=attribute,
                        defaults={"value": selected_values[0]},
                    )
                    count += 1
                else:
                    attribute_values.delete()
        return count

    def handle(self, *args, **options):
        # Демо-дані містять відомі паролі — запускаємо лише в режимі розробки.
        if not settings.DEBUG:
            raise CommandError("seed_demo призначена лише для розробки (потрібен DEBUG=True).")

        # Файл політики перевіряємо одразу, щоб не створити половину даних і не впасти в кінці.
        policy_path = Path(settings.BASE_DIR) / "seed" / "policy_multishop.txt"
        if not policy_path.is_file():
            raise CommandError(f"Файл політики не знайдено: {policy_path}")
        try:
            validate_category_data()
            for name, path, kind, *_ in PRODUCTS:
                problem = validate_demo_product(path, kind)
                if problem:
                    raise ValueError(f"{name}: {problem}")
        except ValueError as exc:
            raise CommandError(f"Помилка у схемі каталогу:\n{exc}") from exc

        # 1. Налаштування сайту та блоки головної
        SiteSettings.get_solo()
        for key, title, text, order in SITE_BLOCKS:
            SiteBlock.objects.get_or_create(key=key, defaults={"title": title, "text": text, "order": order})
        self.stdout.write("Налаштування сайту та блоки головної — готово")

        # 2. Дерево категорій
        categories_before = Category.objects.count()
        self.create_categories(TREE)
        self.stdout.write(f"Категорій створено: {Category.objects.count() - categories_before}")

        # 3. Користувачі
        dev = self.create_user("dev", "dev@multishop.local", "dev12345", "user")
        mainadmin = self.create_user("mainadmin", "mainadmin@multishop.local", "mainadmin12345", "admin_main")
        seller = self.create_user("seller1", "seller1@multishop.local", "seller12345", "user")
        for user in (dev, mainadmin, seller):
            Wallet.objects.get_or_create(user=user)
        self.stdout.write("Користувачі: dev (звичайний), mainadmin (головний адмін), seller1")

        # 4. Верифікація та застава продавця (seed обходить правила застави)
        verification, created = SellerVerification.objects.get_or_create(
            user=seller, defaults={"age": 25}
        )
        if created:
            verification.photo.save(
                "seller1_verification.png",
                make_placeholder("Верифікація seller1", 4),
                save=True,
            )

        required_deposit = Decimal("500")
        SellerDeposit.objects.update_or_create(
            seller=seller, defaults={"required_mc": required_deposit}
        )
        wallet, _ = Wallet.objects.get_or_create(user=seller)
        CoinIssue.objects.get_or_create(
            issued_by=mainadmin, to_user=seller, defaults={"amount_mc": Decimal("1500")}
        )
        # Баланс і стартові транзакції — лише при першому наповненні,
        # інакше повторний запуск скидав би вже витрачені/зароблені кошти.
        if not WalletTransaction.objects.filter(wallet=wallet).exists():
            wallet.free_balance_mc = Decimal("1000")
            wallet.deposit_locked_mc = required_deposit
            wallet.save(update_fields=["free_balance_mc", "deposit_locked_mc"])
            WalletTransaction.objects.create(
                wallet=wallet, amount_mc=Decimal("1500"), kind="coin_issue", note="Стартові MC від головного адміна"
            )
            WalletTransaction.objects.create(
                wallet=                wallet, amount_mc=Decimal("-500"), kind="deposit_lock", note="Застава продавця 1%"
            )
        self.stdout.write("Верифікація й застава seller1 — готово")

        # 5. Товари
        products_before = Product.objects.count()
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
                    "description": f"{name}. Демонстраційний товар для тестування MultiShop.",
                },
            )
            # Фото ставимо, якщо його ще немає (навіть коли товар уже існував).
            if not product.main_image:
                product.main_image.save(f"{product.slug}.png", make_placeholder(name, index), save=True)
            ProductVariant.objects.update_or_create(
                product=product,
                sku=f"SKU-{index + 1:04d}",
                defaults={"price_mc": Decimal(price), "stock": stock},
            )
        self.stdout.write(f"Товарів створено: {Product.objects.count() - products_before}")

        # 5б. Характеристики та їх значення (для фільтрів каталогу)
        attr_by_cat = self.create_attributes()
        values_count = self.set_attribute_values(attr_by_cat)
        categories_hidden = self.deactivate_obsolete_categories()
        self.stdout.write(f"Застарілих вузлів категорій приховано: {categories_hidden}")
        self.stdout.write(
            f"Характеристик: {Attribute.objects.count()}, значень: {values_count}"
        )

        # 6. Політика
        PolicyVersion.objects.update_or_create(
            number=1,
            defaults={
                "text": policy_path.read_text(encoding="utf-8"),
                "is_current": True,
            },
        )
        self.stdout.write("Політику опубліковано (редакція №1)")

        self.stdout.write(self.style.SUCCESS("Демонстраційні дані готові."))
