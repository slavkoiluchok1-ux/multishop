"""Адмінка ядра: налаштування сайту й блоки головної."""
from django.contrib import admin

from .models import SiteBlock, SiteSettings


class ReadOnlyAdminMixin:
    """Модель лише для перегляду: без додавання, зміни та видалення.

    Чутливі дані (гаманець, замовлення, бани, журнал) змінюються лише через
    власний API, тому в Django Admin їх не можна редагувати напряму.
    """

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Один запис налаштувань: додавати нові не можна, лише редагувати."""

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SiteBlock)
class SiteBlockAdmin(admin.ModelAdmin):
    list_display = ("title", "key", "order")
    list_editable = ("order",)
    search_fields = ("title", "key", "text")
    prepopulated_fields = {"key": ("title",)}
