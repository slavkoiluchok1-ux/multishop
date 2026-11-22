"""Політика: редакції та прийняття."""
from django.db import models


class PolicyVersion(models.Model):
    """Редакція політики."""

    number = models.PositiveIntegerField("Номер редакції", unique=True)
    text = models.TextField("Текст політики")
    published_at = models.DateTimeField("Опубліковано", auto_now_add=True)
    is_current = models.BooleanField("Поточна редакція", default=True)

    class Meta:
        ordering = ["-number"]
        verbose_name = "Редакція політики"
        verbose_name_plural = "Редакції політики"

    def __str__(self):
        return f"Політика №{self.number}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_current:
            # Поточною може бути лише одна редакція
            PolicyVersion.objects.exclude(pk=self.pk).update(is_current=False)


class PolicyAcceptance(models.Model):
    """Факт прийняття редакції політики користувачем."""

    user = models.ForeignKey(
        "accounts.CustomUser", on_delete=models.CASCADE, related_name="policy_acceptances"
    )
    version = models.ForeignKey(
        PolicyVersion, on_delete=models.CASCADE, related_name="acceptances"
    )
    accepted_at = models.DateTimeField("Прийнято", auto_now_add=True)

    class Meta:
        unique_together = ("user", "version")
        verbose_name = "Прийняття політики"
        verbose_name_plural = "Прийняття політики"

    def __str__(self):
        return f"{self.user} прийняв політику №{self.version.number}"
