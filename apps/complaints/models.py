"""Скарги й типи скарг. На Етапі 3 використовується подання зі скасуванням."""
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

from apps.core.storage import private_storage, validate_upload

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager


class ComplaintType(models.Model):
    """Вузол дерева скарг, наповнений із COMPLAINTS_PLAN."""

    id: int
    parent_id: int | None

    VIEW_CHOICES = [
        ("orders", "На замовлення"),
        ("products", "На товари"),
        ("users_content", "На користувачів і вміст"),
    ]

    name = models.CharField("Назва", max_length=200)
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )
    path = models.CharField("Шлях", max_length=800, blank=True)
    view = models.CharField("Розділ", max_length=20, choices=VIEW_CHOICES)
    required_fields = models.JSONField("Обов'язкові поля", default=list, blank=True)
    evidence_required = models.JSONField("Рекомендовані докази", default=list, blank=True)
    handler_rights = models.JSONField("Права обробників", default=list, blank=True)
    default_consequence = models.JSONField("Рекомендований наслідок", default=list, blank=True)
    is_active = models.BooleanField("Активний", default=True)

    class Meta:
        ordering = ["view", "path", "id"]
        verbose_name = "Тип скарги"
        verbose_name_plural = "Типи скарг"

    def save(self, *args, **kwargs):
        parent = self.parent
        self.path = f"{parent.path} › {self.name}" if parent is not None else self.name
        super().save(*args, **kwargs)

    @property
    def is_leaf(self):
        return not ComplaintType.objects.filter(
            parent=self, is_active=True
        ).exists()

    def __str__(self):
        return self.path or self.name


class Complaint(models.Model):
    """Скарга користувача. Наслідки та поля форми зберігаються як JSON."""

    id: int
    author_id: int
    complaint_type_id: int
    order_id: int | None
    product_id: int | None
    target_user_id: int | None
    comment_id: int | None
    handled_by_id: int | None
    evidence: "RelatedManager[ComplaintEvidence]"
    comments: "RelatedManager[ComplaintComment]"

    TARGET_CHOICES = [
        ("order", "Замовлення"),
        ("product", "Товар"),
        ("user", "Користувач"),
        ("comment", "Коментар"),
        ("admin", "Адміністратор"),
    ]
    STATUS_CHOICES = [
        ("new", "Нова"),
        ("in_review", "Розглядається"),
        ("upheld", "Підтверджена"),
        ("rejected", "Відхилена"),
        ("false", "Хибна"),
    ]
    CONSEQUENCE_CHOICES = [
        ("none", "Без наслідків"),
        ("warning", "Попередження"),
        ("hide_product", "Приховати товар"),
        ("delete_product", "Видалити товар"),
        ("delete_comment", "Видалити коментар"),
        ("request_fix", "Попросити виправити"),
        ("refund", "Повернути кошти"),
        ("pay_seller", "Виплатити продавцю"),
        ("cancel_order", "Скасувати замовлення"),
        ("deposit_forfeit", "Втрата застави"),
        ("restrict_complaints", "Обмежити скарги"),
        ("revoke_rights", "Зняти права"),
        ("ban", "Бан"),
    ]

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="complaints_made"
    )
    complaint_type = models.ForeignKey(
        ComplaintType, on_delete=models.PROTECT, related_name="complaints"
    )
    target_kind = models.CharField("Тип об'єкта", max_length=16, choices=TARGET_CHOICES)
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="complaints"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="complaints"
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaints_against",
    )
    comment = models.ForeignKey(
        "catalog.Review", on_delete=models.SET_NULL, null=True, blank=True, related_name="complaints"
    )
    text = models.TextField("Текст")
    status = models.CharField("Статус", max_length=16, choices=STATUS_CHOICES, default="new")
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaints_handled",
    )
    consequences = models.JSONField("Наслідки", default=list, blank=True)
    refund_amount_mc = models.DecimalField(
        "Сума часткового повернення, MC",
        max_digits=15,
        decimal_places=6,
        null=True,
        blank=True,
    )
    fields = models.JSONField("Поля форми", default=dict, blank=True)
    resolution = models.TextField("Рішення", blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)
    resolved_at = models.DateTimeField("Вирішено", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["author", "status", "created_at"],
                name="complaint_author_status_idx",
            ),
            models.Index(
                fields=["target_user", "status", "created_at"],
                name="complaint_target_status_idx",
            ),
        ]
        verbose_name = "Скарга"
        verbose_name_plural = "Скарги"

    def __str__(self):
        return f"{self.complaint_type.path} — #{self.pk}"


class ComplaintEvidence(models.Model):
    """Приватний доказ до скарги."""

    id: int
    complaint_id: int

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="evidence")
    file = models.FileField(
        "Доказ",
        upload_to="complaint_evidence/",
        storage=private_storage,
        validators=[validate_upload],
    )
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        verbose_name = "Доказ скарги"
        verbose_name_plural = "Докази скарг"


class ComplaintComment(models.Model):
    """Коментар сторони або адміна у скарзі."""

    SIDE_CHOICES = [
        ("complainant", "Скаржник"),
        ("respondent", "Відповідач"),
        ("admin", "Адмін"),
    ]

    id: int
    complaint_id: int
    author_id: int

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="complaint_comments"
    )
    side = models.CharField("Сторона", max_length=16, choices=SIDE_CHOICES)
    text = models.TextField("Текст")
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Коментар скарги"
        verbose_name_plural = "Коментарі скарг"
