"""Адмінка замовлень (замовлення, позиції, історія, повернення — лише перегляд)."""
from django.contrib import admin

from apps.core.admin import ReadOnlyAdminMixin

from .models import (
    DownloadGrant,
    Order,
    OrderCancellation,
    OrderItem,
    OrderStatusHistory,
    Refund,
)


class OrderItemInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ("product_name", "unit_price_mc", "quantity")


class OrderStatusHistoryInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ("old_status", "new_status", "changed_by", "note", "created_at")


@admin.register(Order)
class OrderAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "buyer", "seller", "status", "total_mc", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("id", "buyer__username", "seller__username", "checkout_id")
    readonly_fields = ("checkout_id", "created_at", "paid_at", "received_at")
    inlines = [OrderItemInline, OrderStatusHistoryInline]


@admin.register(OrderCancellation)
class OrderCancellationAdmin(admin.ModelAdmin):
    list_display = ("order", "path", "buyer_agreed", "seller_agreed", "created_at")
    list_filter = ("path", "buyer_agreed", "seller_agreed")
    search_fields = ("order__id", "initiated_by__username")


@admin.register(Refund)
class RefundAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("order", "amount_mc", "fee_returned_mc", "source", "debt_mc", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("order__id",)
    readonly_fields = ("created_at",)


@admin.register(DownloadGrant)
class DownloadGrantAdmin(admin.ModelAdmin):
    list_display = ("order_item", "file", "granted_at")
    search_fields = ("order_item__order__id", "file__title")
    readonly_fields = ("granted_at",)
