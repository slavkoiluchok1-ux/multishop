"""Серіалізатори гаманця."""
from rest_framework import serializers

from .models import Wallet, WalletTransaction


class WalletSerializer(serializers.ModelSerializer):
    total_mc = serializers.DecimalField(max_digits=15, decimal_places=6, read_only=True)

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = Wallet
        fields = (
            "free_balance_mc",
            "held_mc",
            "frozen_mc",
            "deposit_locked_mc",
            "debt_mc",
            "total_mc",
        )


class WalletTransactionSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = WalletTransaction
        fields = ("id", "amount_mc", "kind", "kind_display", "note", "created_at")
