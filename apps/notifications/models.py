"""Сповіщення: лише лічильники червоних цифр (секції за етапами)."""
from django.conf import settings
from django.core.cache import cache
from django.db import models

SECTIONS = [
    ("orders", "Замовлення"),
    ("wallet", "Гаманець"),
    ("complaints", "Скарги"),
    ("favorites", "Обране"),
    ("support", "Підтримка"),
    ("moderation", "Модерація"),
    ("admin", "Адмін"),
    ("products", "Товари"),
    ("admin_complaints", "Скарги (адмін)"),
    ("admin_support", "Звернення (адмін)"),
]


class Notification(models.Model):
    """Одне сповіщення для користувача в межах секції."""

    id: int
    user_id: int

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    section = models.CharField("Секція", max_length=20, choices=SECTIONS)
    kind = models.CharField("Тип", max_length=40, blank=True)
    text = models.CharField("Текст", max_length=255)
    payload = models.JSONField("Дані", default=dict, blank=True)
    is_read = models.BooleanField("Прочитано", default=False)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "is_read", "section"],
                name="notification_unread_idx",
            ),
        ]
        verbose_name = "Сповіщення"
        verbose_name_plural = "Сповіщення"

    @staticmethod
    def invalidate_unread_cache(user_id):
        cache.delete(f"notifications:unread:{user_id}")

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        self.invalidate_unread_cache(self.user_id)
        return result

    def delete(self, *args, **kwargs):
        user_id = self.user_id
        result = super().delete(*args, **kwargs)
        self.invalidate_unread_cache(user_id)
        return result

    def __str__(self):
        return f"{self.get_section_display()}: {self.text}"

    def get_section_display(self) -> str:
        return dict(SECTIONS).get(self.section, self.section) or self.section
