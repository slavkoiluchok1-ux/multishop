"""Адмінка сповіщень (для розробника)."""
from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "section", "kind", "text", "is_read", "created_at")
    list_filter = ("section", "is_read")
    search_fields = ("user__username", "text")
