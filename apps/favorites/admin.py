"""Адмінка обраного (для розробника)."""
from django.contrib import admin

from .models import Favorite


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("user", "product", "last_price", "last_status", "created_at")
    list_filter = ("last_status",)
    search_fields = ("user__username", "product__name")
