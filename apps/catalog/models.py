#Каталог: категорії-дерево, характеристики, товари, варіанти, файли, відгуки
import secrets
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from django.conf import settings
from django.core.cache import cache
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import slugify

from apps.core.storage import private_storage, validate_image, validate_upload

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager

# Максимальна ціна: SQLite точна лише до ~15 значущих цифр
MAX_PRICE_MC = Decimal("999999999")

PRODUCT_TYPES = [
    ("physical", "Фізичний товар"),
    ("digital_file", "Цифровий файл"),
    ("digital_key", "Ключ, акаунт, ліцензія"),
    ("service", "Послуга"),
    ("subscription", "Підписка"),
]

PRODUCT_STATUSES = [
    ("active", "У продажу"),
    ("inactive", "Продаж зупинено"),
    ("sold_out", "Закінчився"),
    ("hidden", "Прихований"),
]

# Модерація адміністратором: блокування оборотне, видалення — м'яке
MODERATION_CHOICES = [
    ("ok", "Без обмежень"),
    ("blocked", "Заблоковано адміністратором"),
    ("deleted", "Видалено адміністратором"),
]


def unique_slug(model, name, instance_pk=None):
    """Унікальний slug із назви (українські літери зберігаємо)."""
    base = slugify(name, allow_unicode=True) or "item"
    slug = base
    n = 2
    qs = model.objects.all()
    if instance_pk:
        qs = qs.exclude(pk=instance_pk)
    while qs.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


class Category(models.Model):
    """Вузол дерева категорій (2–5 рівнів). Товар належить одному листку."""

    id: int
    parent_id: int | None

    name = models.CharField("Назва", max_length=200)
    slug = models.SlugField("Slug", max_length=220, unique=True, blank=True)
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children",
        verbose_name="Батьківський вузол",
    )
    path = models.CharField("Шлях", max_length=600, blank=True)
    is_active = models.BooleanField("Активна", default=True)
    order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "Категорія"
        verbose_name_plural = "Категорії"

    def __str__(self):
        return self.path or self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Category, self.name, self.pk)
        # Шлях «Батько › Нащадок» — для breadcrumbs і показу
        self.path = f"{self.parent.path} › {self.name}" if self.parent else self.name
        super().save(*args, **kwargs)
        cache.delete("catalog:category-map:v1")

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        cache.delete("catalog:category-map:v1")
        return result

    def descendants(self):
        """Усі нащадки, включно із собою."""
        ids = [self.pk]
        frontier = [self.pk]
        while frontier:
            children = list(Category.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
            ids.extend(children)
            frontier = children
        return Category.objects.filter(pk__in=ids)


class Attribute(models.Model):
    """Характеристика вузла, успадковується вниз по дереву."""

    id: int
    category_id: int
    options: models.Manager["AttributeOption"]

    KIND_CHOICES = [
        ("single", "Вибір одного"),
        ("multiple", "Вибір кількох"),
        ("number", "Число або діапазон"),
        ("bool", "Так / Ні"),
    ]

    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="attributes")
    name = models.CharField("Назва", max_length=120)
    kind = models.CharField("Тип", max_length=16, choices=KIND_CHOICES, default="single")
    unit = models.CharField("Одиниця виміру", max_length=32, blank=True)
    required = models.BooleanField("Обов'язкова", default=False)
    order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "Характеристика"
        verbose_name_plural = "Характеристики"

    def __str__(self):
        return f"{self.name} ({self.category.name})"


class AttributeOption(models.Model):
    """Можливе значення характеристики для вибору."""

    id: int
    attribute_id: int

    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name="options")
    value = models.CharField("Значення", max_length=120)

    class Meta:
        ordering = ["value"]
        verbose_name = "Значення характеристики"
        verbose_name_plural = "Значення характеристик"

    def __str__(self):
        return self.value


class Product(models.Model):
    """Товар. Належить продавцю й одному листку дерева категорій."""

    id: int
    moderated_by_id: int | None
    seller_id: int
    category_id: int
    variants: "RelatedManager[ProductVariant]"
    digital_files: "RelatedManager[DigitalFile]"
    images: "RelatedManager[ProductImage]"

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="products"
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    name = models.CharField("Назва", max_length=200)
    slug = models.SlugField("Slug", max_length=220, unique=True, blank=True)
    description = models.TextField("Опис", blank=True)
    brand = models.CharField("Бренд", max_length=120, blank=True)
    portfolio_url = models.URLField("Посилання на портфоліо", max_length=500, blank=True)
    product_type = models.CharField("Вид товару", max_length=16, choices=PRODUCT_TYPES)
    status = models.CharField("Статус", max_length=16, choices=PRODUCT_STATUSES, default="active")
    hidden_token = models.CharField("Токен прихованого товару", max_length=64, blank=True)
    main_image = models.ImageField(
        "Головне фото", upload_to="products/", blank=True, null=True, validators=[validate_image]
    )
    moderation = models.CharField(
        "Модерація", max_length=16, choices=MODERATION_CHOICES, default="ok"
    )
    moderation_reason = models.TextField("Причина модерації", blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderated_products",
        verbose_name="Хто змінив модерацію",
    )
    moderated_at = models.DateTimeField("Дата модерації", null=True, blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)
    updated_at = models.DateTimeField("Оновлено", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "moderation", "created_at"],
                name="product_status_mod_created_idx",
            ),
        ]
        verbose_name = "Товар"
        verbose_name_plural = "Товари"

    def __str__(self):
        return cast(str, self.name)

    def get_product_type_display(self) -> str:
        return cast(str, dict(PRODUCT_TYPES).get(self.product_type, self.product_type))

    def get_status_display(self) -> str:
        return cast(str, dict(PRODUCT_STATUSES).get(self.status, self.status))

    def get_moderation_display(self) -> str:
        return cast(str, dict(MODERATION_CHOICES).get(self.moderation, self.moderation))

    @property
    def is_moderated(self) -> bool:
        """True, якщо товар заблокував або видалив адміністратор."""
        return self.moderation != "ok"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Product, self.name, self.pk)
        if self.status == "hidden" and not self.hidden_token:
            self.hidden_token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)

    @property
    def min_price_mc(self) -> Decimal | None:
        """Найменша ціна серед варіантів (для картки каталогу)."""
        return ProductVariant.objects.filter(product=self).aggregate(
            m=models.Min("price_mc")
        )["m"]


class ProductVariant(models.Model):
    """Варіант товару. Ціна й залишок зберігаються тут."""

    id: int
    product_id: int
    attribute_values: "RelatedManager[VariantAttributeValue]"
    digital_files: "RelatedManager[DigitalFile]"

    LICENSE_CHOICES = [
        ("personal", "Персональна"),
        ("commercial", "Комерційна"),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField("Артикул", max_length=64, blank=True)
    price_mc = models.DecimalField(
        "Ціна, MC",
        max_digits=15,
        decimal_places=6,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(MAX_PRICE_MC)],
    )
    stock = models.PositiveIntegerField("Залишок", default=0)
    # Для цифрових файлів: персональна чи комерційна ліцензія
    license_type = models.CharField(
        "Ліцензія", max_length=16, choices=LICENSE_CHOICES, blank=True
    )
    image = models.ImageField(
        "Фото варіанта", upload_to="products/", blank=True, null=True, validators=[validate_image]
    )

    class Meta:
        ordering = ["price_mc"]
        verbose_name = "Варіант товару"
        verbose_name_plural = "Варіанти товарів"

    def __str__(self):
        return f"{self.product.name} — {self.price_mc} MC"

    def get_license_type_display(self) -> str:
        return dict(self.LICENSE_CHOICES).get(self.license_type, self.license_type) or self.license_type


def sync_stock_status(product: "Product") -> "Product":
    """Автоматичний статус за залишком: active <-> sold_out.

    Коли залишок усіх варіантів падає до нуля — «Закінчився», коли знову
    більший за нуль — «У продажу». hidden та inactive не чіпаємо.
    """
    total = ProductVariant.objects.filter(product=product).aggregate(
        total=models.Sum("stock")
    )["total"] or 0
    if product.status == "active" and total < 1:
        product.status = "sold_out"
    elif product.status == "sold_out" and total > 0:
        product.status = "active"
    else:
        return product
    product.save(update_fields=["status"])
    return product


class VariantAttributeValue(models.Model):
    """Одне зі значень характеристики для конкретного варіанта."""

    id: int
    variant_id: int
    attribute_id: int

    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="attribute_values")
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name="values")
    value = models.CharField("Значення", max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("variant", "attribute", "value"),
                name="uniq_variant_attribute_value",
            )
        ]
        verbose_name = "Значення характеристики варіанта"
        verbose_name_plural = "Значення характеристик варіантів"

    def __str__(self):
        return f"{self.attribute.name}: {self.value}"


class ProductImage(models.Model):
    """Додаткове фото товару."""

    id: int
    product_id: int
    variant_id: int | None

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    variant = models.ForeignKey(
        ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name="images"
    )
    image = models.ImageField("Фото", upload_to="products/", validators=[validate_image])
    order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Фото товару"
        verbose_name_plural = "Фото товарів"

    def __str__(self):
        return f"Фото {self.product.name}"


class DigitalFile(models.Model):
    """Файл цифрового товару. Платний — лише покупцю, пробний — усім."""

    id: int
    product_id: int
    variant_id: int | None

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="digital_files")
    variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, null=True, blank=True, related_name="digital_files"
    )
    file = models.FileField(
        "Файл", upload_to="digital_files/", storage=private_storage, validators=[validate_upload]
    )
    is_trial = models.BooleanField("Пробна версія", default=False)
    title = models.CharField("Назва файлу", max_length=200, blank=True)

    class Meta:
        verbose_name = "Цифровий файл"
        verbose_name_plural = "Цифрові файли"

    def __str__(self):
        return self.title or f"Файл {self.product.name}"


class Review(models.Model):
    """Коментар до товару. Лише після купівлі."""

    id: int
    product_id: int
    author_id: int
    order_id: int | None
    media: "RelatedManager[ReviewMedia]"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="reviews"
    )
    text = models.TextField("Текст")
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Відгук"
        verbose_name_plural = "Відгуки"

    def __str__(self):
        return f"Відгук {self.author} → {self.product.name}"


class ReviewMedia(models.Model):
    """Фото або відео до відгуку (до 100 МБ)."""

    KIND_CHOICES = [
        ("photo", "Фото"),
        ("video", "Відео"),
    ]

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="media")
    id: int
    review_id: int
    file = models.FileField("Файл", upload_to="reviews/", validators=[validate_upload])
    kind = models.CharField("Тип", max_length=8, choices=KIND_CHOICES)

    class Meta:
        verbose_name = "Медіа відгуку"
        verbose_name_plural = "Медіа відгуків"

    def get_kind_display(self) -> str:
        return dict(self.KIND_CHOICES).get(self.kind, self.kind) or self.kind

    def __str__(self) -> str:
        return f"{self.get_kind_display()} до відгуку #{self.review_id}"
