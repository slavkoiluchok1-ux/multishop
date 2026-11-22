"""Адмінка політики."""
from django.contrib import admin

from .models import PolicyAcceptance, PolicyVersion


@admin.register(PolicyVersion)
class PolicyVersionAdmin(admin.ModelAdmin):
    list_display = ("number", "is_current", "published_at")
    list_filter = ("is_current",)
    search_fields = ("text",)
    readonly_fields = ("published_at",)


@admin.register(PolicyAcceptance)
class PolicyAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("user", "version", "accepted_at")
    list_filter = ("version",)
    search_fields = ("user__username",)
    readonly_fields = ("accepted_at",)
