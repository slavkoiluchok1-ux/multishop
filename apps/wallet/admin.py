"""Адмінка гаманця: баланси, транзакції, ескроу, застави (лише перегляд)."""
from django.contrib import admin

from apps.core.admin import ReadOnlyAdminMixin

from .models import CoinIssue, EscrowHold, FrozenPayout, SellerDeposit, Wallet, WalletTransaction


@admin.register(Wallet)
class WalletAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "user",
        "free_balance_mc",
        "held_mc",
        "frozen_mc",
        "deposit_locked_mc",
        "debt_mc",
    )
    search_fields = ("user__username",)
    readonly_fields = ("updated_at",)


@admin.register(WalletTransaction)
class WalletTransactionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("wallet", "kind", "amount_mc", "order", "created_at")
    list_filter = ("kind", "created_at")
    search_fields = ("wallet__user__username", "note")
    readonly_fields = ("created_at",)


@admin.register(CoinIssue)
class CoinIssueAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("issued_by", "to_user", "amount_mc", "created_at")
    search_fields = ("to_user__username", "issued_by__username")
    readonly_fields = ("created_at",)


@admin.register(EscrowHold)
class EscrowHoldAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("order", "buyer", "seller", "amount_mc", "fee_mc", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("buyer__username", "seller__username")


@admin.register(FrozenPayout)
class FrozenPayoutAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("seller", "order", "amount_mc", "release_at", "status")
    list_filter = ("status", "release_at")
    search_fields = ("seller__username",)


@admin.register(SellerDeposit)
class SellerDepositAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("seller", "required_mc", "updated_at")
    search_fields = ("seller__username",)
