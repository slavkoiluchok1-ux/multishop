"""Замовлення, історія, взаємне скасування, повернення й доступ до файлів."""
import hashlib
import unicodedata
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager
    from apps.catalog.models import Review
    from apps.wallet.models import EscrowHold


def normalize_activation_code(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def activation_code_key(value: str) -> str:
    return hashlib.sha256(normalize_activation_code(value).encode("utf-8")).hexdigest()


class Order(models.Model):
    """Одне замовлення одного продавця в межах спільного оформлення."""

    id: int
    buyer_id: int
    seller_id: int
    items: "RelatedManager[OrderItem]"
    comments: "RelatedManager[OrderComment]"
    status_history: "RelatedManager[OrderStatusHistory]"
    cancellation: "OrderCancellation"
    refunds: "RelatedManager[Refund]"
    reviews: "RelatedManager[Review]"
    escrow_holds: "RelatedManager[EscrowHold]"

    STATUS_CHOICES = [
        ("pending", "Очікує оплати"),
        ("paid", "Оплачено"),
        ("shipped", "Передано / відправлено"),
        ("delivered", "Отримано"),
        ("cancelled", "Скасовано"),
        ("dispute", "Спір"),
    ]

    checkout_id = models.UUIDField("Оформлення", default=uuid.uuid4, db_index=True)
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders_bought",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders_sold",
    )
    status = models.CharField("Статус", max_length=16, choices=STATUS_CHOICES, default="paid")
    total_mc = models.DecimalField("Сума, MC", max_digits=15, decimal_places=6)
    fee_mc = models.DecimalField(
        "Комісія, MC", max_digits=15, decimal_places=6, default=Decimal("0")
    )
    contact_email = models.EmailField("Email для зв'язку", blank=True)
    contact_phone = models.CharField("Телефон", max_length=32, blank=True)
    contact_telegram = models.CharField("Telegram", max_length=64, blank=True)
    contact_other = models.TextField("Інші контакти", blank=True)
    delivery_address = models.TextField("Адреса доставки", blank=True)
    recipient_name = models.CharField("Отримувач", max_length=150, blank=True)
    paid_at = models.DateTimeField("Оплачено", null=True, blank=True)
    received_at = models.DateTimeField("Підтверджено отримання", null=True, blank=True)
    complaint_deadline_at = models.DateTimeField("Кінцевий строк скарги", null=True, blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Замовлення"
        verbose_name_plural = "Замовлення"

    def __str__(self):
        return f"Замовлення #{self.pk}"

    def get_status_display(self) -> str:
        return dict(self.STATUS_CHOICES).get(self.status, self.status) or self.status


class OrderItem(models.Model):
    """Знімок придбаної позиції, який не змінюється разом із товаром."""

    id: int
    order_id: int
    variant_id: int
    downloads: "RelatedManager[DownloadGrant]"
    activation_codes: "RelatedManager[ActivationCode]"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(
        "catalog.ProductVariant", on_delete=models.PROTECT, related_name="order_items"
    )
    product_name = models.CharField("Назва товару", max_length=200)
    unit_price_mc = models.DecimalField("Ціна за одиницю, MC", max_digits=15, decimal_places=6)
    quantity = models.PositiveIntegerField("Кількість")

    class Meta:
        verbose_name = "Позиція замовлення"
        verbose_name_plural = "Позиції замовлення"

    def __str__(self):
        return f"{self.product_name} × {self.quantity}"


class ActivationCode(models.Model):
    """Один приватний код на одиницю товару; код не можна використати повторно."""

    variant_id: int

    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.PROTECT,
        related_name="activation_codes",
    )
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="activation_codes",
    )
    code = models.CharField("Код активації", max_length=256)
    code_key = models.CharField("Ключ унікальності коду", max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField("Додано", auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Код активації"
        verbose_name_plural = "Коди активації"
        indexes = [
            models.Index(
                fields=["variant", "order_item"],
                name="activation_variant_order_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        self.code = self.code.strip()
        self.code_key = activation_code_key(self.code)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Код для варіанта #{self.variant_id}"


class OrderStatusHistory(models.Model):
    """Незмінна історія переходів статусу замовлення."""

    id: int
    order_id: int
    changed_by_id: int | None

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    old_status = models.CharField("Попередній статус", max_length=16, blank=True)
    new_status = models.CharField("Новий статус", max_length=16, choices=Order.STATUS_CHOICES)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_status_changes",
    )
    note = models.CharField("Примітка", max_length=255, blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Історія статусу"
        verbose_name_plural = "Історія статусів"

    def get_new_status_display(self) -> str:
        return dict(Order.STATUS_CHOICES).get(self.new_status, self.new_status) or self.new_status


class OrderCancellation(models.Model):
    """Скасування до підтвердження отримання: через скаргу або за згодою."""

    STATUS_CHOICES = [
        ("pending", "Очікує відповіді"),
        ("rejected", "Відхилено"),
        ("withdrawn", "Відкликано"),
        ("completed", "Завершено"),
    ]

    PATH_CHOICES = [
        ("complaint", "Через скаргу"),
        ("mutual", "За згодою сторін"),
    ]

    id: int
    order_id: int
    initiated_by_id: int
    complaint_id: int | None

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="cancellation")
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="initiated_cancellations"
    )
    path = models.CharField("Шлях", max_length=16, choices=PATH_CHOICES)
    status = models.CharField(
        "Стан пропозиції", max_length=16, choices=STATUS_CHOICES, default="pending"
    )
    complaint = models.ForeignKey(
        "complaints.Complaint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancellations",
    )
    buyer_agreed = models.BooleanField("Покупець погодив", default=False)
    seller_agreed = models.BooleanField("Продавець погодив", default=False)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        verbose_name = "Скасування замовлення"
        verbose_name_plural = "Скасування замовлень"


class OrderComment(models.Model):
    """Повідомлення покупця або продавця в обговоренні замовлення."""

    id: int
    order_id: int
    author_id: int

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="order_comments"
    )
    text = models.CharField("Коментар", max_length=2000)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Коментар до замовлення"
        verbose_name_plural = "Коментарі до замовлень"


class DownloadGrant(models.Model):
    """Право завантаження платного файлу після оплати."""

    id: int
    order_item_id: int
    file_id: int

    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="downloads")
    file = models.ForeignKey(
        "catalog.DigitalFile", on_delete=models.PROTECT, related_name="grants"
    )
    granted_at = models.DateTimeField("Надано", auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("order_item", "file"), name="uniq_order_item_download_grant"
            )
        ]
        verbose_name = "Дозвіл на завантаження"
        verbose_name_plural = "Дозволи на завантаження"


class Refund(models.Model):
    """Повернення покупцю; списання продавця виконується в порядку правил платформи."""

    SOURCE_CHOICES = [
        ("escrow", "Утримані кошти"),
        ("frozen", "Заморожені кошти"),
        ("seller_balance", "Доступні кошти продавця"),
        ("deposit", "Застава"),
    ]

    id: int
    order_id: int
    approved_by_id: int | None

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="refunds")
    amount_mc = models.DecimalField("Сума покупцю, MC", max_digits=15, decimal_places=6)
    fee_returned_mc = models.DecimalField(
        "Анульована комісія, MC", max_digits=15, decimal_places=6, default=Decimal("0")
    )
    source = models.CharField("Джерело", max_length=20, choices=SOURCE_CHOICES)
    debt_mc = models.DecimalField(
        "Створений борг, MC", max_digits=15, decimal_places=6, default=Decimal("0")
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_refunds",
    )
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Повернення"
        verbose_name_plural = "Повернення"
