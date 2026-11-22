"""Ядро: налаштування сайту й блоки головної сторінки."""
from decimal import Decimal

from django.core.cache import cache
from django.core.validators import MinValueValidator
from django.db import models


class SiteSettings(models.Model):
    """Глобальні налаштування сайту. Має бути один запис."""

    commission_percent = models.DecimalField(
        "Комісія сайту, %", max_digits=5, decimal_places=2, default=Decimal("3")
    )
    mc_to_uah = models.DecimalField(
        "1 MC у гривнях", max_digits=10, decimal_places=2, default=Decimal("10")
    )
    deposit_percent = models.DecimalField(
        "Застава, %", max_digits=5, decimal_places=2, default=Decimal("1")
    )
    max_upload_mb = models.PositiveIntegerField("Максимум завантаження, МБ", default=100)
    max_decimals = models.PositiveSmallIntegerField("Знаків після коми", default=6)
    payout_freeze_days = models.PositiveSmallIntegerField("Замороження виплати, днів", default=14)
    complaint_deadline_days = models.PositiveSmallIntegerField("Строк скарги, днів", default=14)
    false_complaints_limit = models.PositiveSmallIntegerField("Ліміт хибних скарг", default=3)
    complaints_block_first_days = models.PositiveSmallIntegerField(
        "Блок скарг (перший раз), днів", default=30
    )
    complaints_block_repeat_days = models.PositiveSmallIntegerField(
        "Блок скарг (повторно), днів", default=365
    )

    class Meta:
        verbose_name = "Налаштування сайту"
        verbose_name_plural = "Налаштування сайту"

    def __str__(self):
        return "Налаштування сайту"

    def save(self, *args, **kwargs):
        # Завжди один запис
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SiteBlock(models.Model):
    """Блок головної сторінки з налаштовуваними розміром і виглядом."""

    LAYOUT_CHOICES = [
        ("compact", "Компактний"),
        ("standard", "Стандартний"),
        ("wide", "Широкий"),
    ]
    STYLE_CHOICES = [
        ("paper", "Світлий"),
        ("accent", "Акцентний"),
        ("dark", "Темний"),
    ]

    key = models.SlugField("Ключ", max_length=64, unique=True)
    eyebrow = models.CharField("Короткий підпис", max_length=80, blank=True)
    title = models.CharField("Заголовок", max_length=200)
    text = models.TextField("Текст")
    layout = models.CharField(
        "Розмір блоку", max_length=12, choices=LAYOUT_CHOICES, default="standard"
    )
    style = models.CharField(
        "Стиль блоку", max_length=12, choices=STYLE_CHOICES, default="paper"
    )
    order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Блок головної"
        verbose_name_plural = "Блоки головної"

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        cache.delete("site:homepage-blocks:v1")
        return result

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        cache.delete("site:homepage-blocks:v1")
        return result

    def __str__(self):
        return self.title
