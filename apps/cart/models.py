"""Кошик: у базі для залогінених, у сесії для гостей."""
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

from apps.catalog.models import ProductVariant

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager


class Cart(models.Model):
    """Кошик користувача (user) або гостя (session_key)."""

    id: int
    user_id: int | None
    items: "RelatedManager[CartItem]"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cart",
        verbose_name="Користувач",
    )
    session_key = models.CharField("Ключ сесії", max_length=64, blank=True, db_index=True)
    updated_at = models.DateTimeField("Оновлено", auto_now=True)

    class Meta:
        verbose_name = "Кошик"
        verbose_name_plural = "Кошики"

    def __str__(self):
        return f"Кошик {self.user or self.session_key}"


class CartItem(models.Model):
    """Позиція кошика: варіант і кількість."""

    id: int
    cart_id: int
    variant_id: int

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="cart_items"
    )
    quantity = models.PositiveIntegerField("Кількість", default=1)
    # Ціна на момент додавання — щоб показати «Ціна товару змінилась»
    price_at_add = models.DecimalField("Ціна при додаванні", max_digits=15, decimal_places=6)

    class Meta:
        unique_together = ("cart", "variant")
        verbose_name = "Позиція кошика"
        verbose_name_plural = "Позиції кошика"

    def __str__(self):
        return f"{self.variant} × {self.quantity}"
