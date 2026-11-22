"""Гаманець MultiShop: баланс за частинами, транзакції, ескроу, застава."""
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import RelatedManager

# Усі суми — Decimal(15,6). Ніколи float.
MONEY = {"max_digits": 15, "decimal_places": 6, "default": Decimal("0")}


class Wallet(models.Model):
    """Гаманець користувача. Баланс складається з частин."""

    id: int
    user_id: int
    transactions: "RelatedManager[WalletTransaction]"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet"
    )
    free_balance_mc = models.DecimalField("Доступні, MC", **MONEY)
    held_mc = models.DecimalField("Утримані, MC", **MONEY)
    frozen_mc = models.DecimalField("Заморожені, MC", **MONEY)
    deposit_locked_mc = models.DecimalField("Заблокована застава, MC", **MONEY)
    debt_mc = models.DecimalField("Борг, MC", **MONEY)
    updated_at = models.DateTimeField("Оновлено", auto_now=True)

    class Meta:
        verbose_name = "Гаманець"
        verbose_name_plural = "Гаманці"

    def __str__(self):
        return f"Гаманець {self.user}"

    @property
    def total_mc(self):
        """Загальна сума всіх частин балансу."""
        return (
            self.free_balance_mc
            + self.held_mc
            + self.frozen_mc
            + self.deposit_locked_mc
        )


class WalletTransaction(models.Model):
    """Рух коштів у гаманці. Історія для показу користувачу."""

    KIND_CHOICES = [
        ("coin_issue", "Видача MC головним адміном"),
        ("escrow_hold", "Утримання коштів покупця"),
        ("escrow_to_frozen", "З утриманих у заморожені продавцю"),
        ("frozen_release", "Звільнення заморожених у доступні"),
        ("fee", "Комісія сайту"),
        ("refund", "Повернення коштів покупцю"),
        ("deposit_lock", "Блокування застави"),
        ("deposit_return", "Повернення застави"),
        ("deposit_forfeit", "Втрата застави"),
        ("debt_add", "Нарахування боргу"),
        ("debt_pay", "Погашення боргу"),
    ]

    id: int
    wallet_id: int
    order_id: int | None

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    amount_mc = models.DecimalField("Сума, MC", max_digits=15, decimal_places=6)
    kind = models.CharField("Тип", max_length=32, choices=KIND_CHOICES)
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions"
    )
    note = models.CharField("Примітка", max_length=255, blank=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["wallet", "created_at"], name="wallet_tx_wallet_created_idx"),
        ]
        verbose_name = "Транзакція"
        verbose_name_plural = "Транзакції"

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount_mc} MC"

    def get_kind_display(self) -> str:
        return dict(self.KIND_CHOICES).get(self.kind, self.kind) or self.kind


class CoinIssue(models.Model):
    """Видача мульти коінів. Робить лише головний адмін."""

    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="coin_issues_made",
        verbose_name="Хто видав",
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coin_issues_received",
        verbose_name="Кому видано",
    )
    amount_mc = models.DecimalField("Сума, MC", max_digits=15, decimal_places=6)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Видача MC"
        verbose_name_plural = "Видачі MC"

    def __str__(self):
        return f"{self.amount_mc} MC → {self.to_user}"


class EscrowHold(models.Model):
    """Утримані кошти покупця за замовлення."""

    STATUS_CHOICES = [
        ("held", "Утримано"),
        ("released", "Виплачено продавцю"),
        ("refunded", "Повернено покупцю"),
    ]

    id: int
    order_id: int
    buyer_id: int
    seller_id: int

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="escrow_holds")
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="escrow_as_buyer"
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="escrow_as_seller"
    )
    amount_mc = models.DecimalField("Сума, MC", max_digits=15, decimal_places=6)
    fee_mc = models.DecimalField("Комісія, MC", **MONEY)
    status = models.CharField("Статус", max_length=16, choices=STATUS_CHOICES, default="held")
    created_at = models.DateTimeField("Створено", auto_now_add=True)
    resolved_at = models.DateTimeField("Вирішено", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Утримані кошти"
        verbose_name_plural = "Утримані кошти"

    def __str__(self):
        return f"Ескроу {self.amount_mc} MC ({self.get_status_display()})"

    def get_status_display(self) -> str:
        return dict(self.STATUS_CHOICES).get(self.status, self.status) or self.status


class FrozenPayout(models.Model):
    """Виплата продавцю, заморожена на 14 днів (або повернення застави)."""

    STATUS_CHOICES = [
        ("frozen", "Заморожено"),
        ("released", "Звільнено"),
        ("used_for_refund", "Пішло на повернення"),
    ]

    id: int
    seller_id: int
    order_id: int | None

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="frozen_payouts"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="frozen_payouts"
    )
    amount_mc = models.DecimalField("Сума, MC", max_digits=15, decimal_places=6)
    release_at = models.DateTimeField("Звільнити після")
    status = models.CharField("Статус", max_length=16, choices=STATUS_CHOICES, default="frozen")
    created_at = models.DateTimeField("Створено", auto_now_add=True)
    resolved_at = models.DateTimeField("Вирішено", null=True, blank=True)

    class Meta:
        ordering = ["release_at"]
        verbose_name = "Заморожена виплата"
        verbose_name_plural = "Заморожені виплати"

    def __str__(self):
        return f"Заморожено {self.amount_mc} MC → {self.seller}"


class SellerDeposit(models.Model):
    """Розрахункова потрібна застава продавця (1% від ціни × залишок).

    Фактично заблокована сума зберігається лише в Wallet.deposit_locked_mc.
    """

    id: int
    seller_id: int

    seller = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deposit"
    )
    required_mc = models.DecimalField("Потрібна застава, MC", **MONEY)
    updated_at = models.DateTimeField("Оновлено", auto_now=True)

    class Meta:
        verbose_name = "Застава продавця"
        verbose_name_plural = "Застави продавців"

    def __str__(self):
        return f"Застава {self.seller}: {self.required_mc} MC"
