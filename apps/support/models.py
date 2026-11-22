"""Підтримка: звернення, повідомлення у зверненні, відновлення акаунта.

Звернення може подати й гість (тоді user порожній, а контакти заповнені).
Забанені писати можуть — звернення не блокуються.
"""
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

from apps.core.storage import private_storage, validate_upload

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager


class SupportTicket(models.Model):
    """Звернення в підтримку від користувача або гостя."""

    id: int
    user_id: int | None
    assigned_admin_id: int | None
    parent_ticket_id: int | None
    appeal_ban_id: int | None
    list_last_message_text: str | None
    list_message_count: int
    messages: "RelatedManager[SupportMessage]"
    recoveries: "RelatedManager[AccountRecovery]"
    action_history: "RelatedManager[SupportAction]"

    STATUS_CHOICES = [
        ("open", "Відкрите"),
        ("answered", "Відповіли"),
        ("closed", "Закрите"),
    ]
    TYPE_CHOICES = [
        ("unban", "Розбан або оскарження блокування"),
        ("appeal", "Апеляція"),
        ("bug", "Виправлення помилки"),
        ("account", "Проблема з акаунтом"),
        ("payment", "Оплата або баланс MC"),
        ("order", "Замовлення або доставка"),
        ("product", "Товар або продавець"),
        ("policy", "Питання щодо правил"),
        ("suggestion", "Пропозиція або побажання"),
        ("other", "Інше"),
    ]
    BUG_STATUS_CHOICES = [
        ("accepted", "Прийнято"),
        ("in_progress", "В роботі"),
        ("fixed", "Виправлено"),
        ("not_reproduced", "Не відтворюється"),
    ]
    IDEA_STATUS_CHOICES = [
        ("new", "Нова"),
        ("considering", "Розглядається"),
        ("accepted", "Прийнято"),
        ("rejected", "Відхилено"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
    )
    request_type = models.CharField(
        "Тип звернення", max_length=24, choices=TYPE_CHOICES, default="other"
    )
    contact_email = models.EmailField("Email для зв'язку", blank=True)
    contact_phone = models.CharField("Телефон", max_length=32, blank=True)
    contact_telegram = models.CharField("Telegram", max_length=64, blank=True)
    contact_instagram = models.CharField("Instagram", max_length=64, blank=True)
    contact_viber = models.CharField("Viber", max_length=32, blank=True)
    contact_facebook = models.CharField("Facebook", max_length=128, blank=True)
    contact_whatsapp = models.CharField("WhatsApp", max_length=32, blank=True)
    contact_info = models.CharField("Інші контакти", max_length=255, blank=True)
    target_username = models.CharField("Нік акаунта", max_length=150, blank=True, default="")
    order_reference = models.CharField("Номер замовлення", max_length=32, blank=True, default="")
    subject = models.CharField("Тема", max_length=200)
    desired_outcome = models.TextField("Бажаний результат", blank=True, default="")
    status = models.CharField("Статус", max_length=16, choices=STATUS_CHOICES, default="open")
    assigned_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_support_tickets",
    )
    from_banned = models.BooleanField("Від забаненого", default=False)
    parent_ticket = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appeals",
        verbose_name="Попереднє звернення",
    )
    appeal_ban = models.ForeignKey(
        "accounts.Ban",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_appeals",
        verbose_name="Оскаржений бан",
    )
    appeal_rejection = models.OneToOneField(
        "SupportAction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appeal_ticket",
        verbose_name="Оскаржена відмова",
    )
    bug_status = models.CharField(
        "Статус помилки", max_length=24, choices=BUG_STATUS_CHOICES, blank=True
    )
    idea_status = models.CharField(
        "Статус пропозиції", max_length=24, choices=IDEA_STATUS_CHOICES, blank=True
    )
    internal_note = models.TextField("Внутрішня примітка", blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="support_status_created_idx"),
            models.Index(
                fields=["request_type", "status", "created_at"],
                name="support_type_st_created_idx",
            ),
        ]
        verbose_name = "Звернення"
        verbose_name_plural = "Звернення"

    def __str__(self):
        return f"Звернення #{self.pk}: {self.subject}"

    def get_request_type_display(self) -> str:
        return dict(self.TYPE_CHOICES).get(self.request_type, self.request_type) or self.request_type

    def get_bug_status_display(self) -> str:
        return dict(self.BUG_STATUS_CHOICES).get(self.bug_status, self.bug_status) or self.bug_status

    def get_idea_status_display(self) -> str:
        return dict(self.IDEA_STATUS_CHOICES).get(self.idea_status, self.idea_status) or self.idea_status


class SupportAction(models.Model):
    """Адмінська дія або внутрішня примітка, видима лише працівникам підтримки."""

    id: int

    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="action_history"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_actions",
    )
    action = models.CharField("Дія", max_length=64)
    reason = models.TextField("Причина", blank=True)
    metadata = models.JSONField("Додаткові дані", default=dict, blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Дія підтримки"
        verbose_name_plural = "Історія дій підтримки"


class SupportMessage(models.Model):
    """Повідомлення у зверненні: від людини (гість/користувач) або адміна."""

    id: int
    ticket_id: int
    sender_id: int | None

    SENDER_CHOICES = [
        ("user", "Людина"),
        ("admin", "Адмін"),
    ]

    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_messages",
    )
    from_side = models.CharField("Сторона", max_length=8, choices=SENDER_CHOICES, default="user")
    text = models.TextField("Текст")
    attachment = models.FileField(
        "Вкладення",
        upload_to="support/",
        storage=private_storage,
        validators=[validate_upload],
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Повідомлення звернення"
        verbose_name_plural = "Повідомлення звернень"

    def __str__(self):
        return f"Повідомлення #{self.pk} до звернення #{self.ticket_id}"


class AccountRecovery(models.Model):
    """Відновлення акаунта через підтримку. Пароль генерує система."""

    id: int
    ticket_id: int
    target_user_id: int
    admin_id: int | None

    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="recoveries"
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_recoveries",
    )
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_recoveries",
    )
    contacted_via = models.CharField("Як зв'язалися", max_length=128, blank=True)
    note = models.TextField("Нотатка", blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Відновлення акаунта"
        verbose_name_plural = "Відновлення акаунтів"

    def __str__(self):
        return f"Відновлення {self.target_user} (звернення #{self.ticket_id})"
