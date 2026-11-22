"""Обране: товари, за якими стежить користувач."""
from django.conf import settings
from django.db import models

from apps.catalog.models import Product


class Favorite(models.Model):
    """Обраний товар. last_price/last_status — для сповіщень про зміни."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites"
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="favorites")
    last_price = models.DecimalField(
        "Остання ціна", max_digits=15, decimal_places=6, null=True, blank=True
    )
    last_status = models.CharField("Останній статус", max_length=16, blank=True)
    created_at = models.DateTimeField("Додано", auto_now_add=True)

    class Meta:
        unique_together = ("user", "product")
        ordering = ["-created_at"]
        verbose_name = "Обране"
        verbose_name_plural = "Обране"

    def __str__(self):
        return f"{self.user} ♥ {self.product.name}"
