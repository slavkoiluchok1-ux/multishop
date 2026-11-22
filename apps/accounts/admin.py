"""Адмінка акаунтів: користувачі, права, профілі, верифікація, бани, журнал."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.core.admin import ReadOnlyAdminMixin

from .models import AdminLog, AdminRight, Ban, CustomUser, SellerVerification, UserProfile


class AdminRightInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = AdminRight
    extra = 0
    can_delete = False


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    extra = 0


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "email", "role", "is_active", "is_staff", "created_at")
    list_filter = ("role", "is_active", "is_staff", "is_superuser")
    search_fields = ("username", "email")
    inlines = [UserProfileInline, AdminRightInline]
    # Роль і права змінюються лише через кабінет адміна, щоб усе потрапляло в AdminLog.
    readonly_fields = ("role", "is_staff", "is_superuser", "groups", "user_permissions")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Контакти", {"fields": ("email",)}),
        ("MultiShop", {"fields": ("role", "avatar", "must_change_password")}),
        ("Статус", {"fields": ("is_active", "last_login", "date_joined")}),
    )
    # Новий користувач завжди звичайний (role=user), рівні видає лише кабінет.
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "email", "password1", "password2")}),
    )


@admin.register(AdminRight)
class AdminRightAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("user", "right")
    list_filter = ("right",)
    search_fields = ("user__username",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "telegram", "delivery_city", "false_complaints_count")
    search_fields = ("user__username", "phone", "telegram")


@admin.register(SellerVerification)
class SellerVerificationAdmin(admin.ModelAdmin):
    list_display = ("user", "age", "is_locked", "created_at")
    list_filter = ("is_locked",)
    search_fields = ("user__username",)
    readonly_fields = ("created_at",)


@admin.register(Ban)
class BanAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("user", "source", "issued_by", "starts_at", "ends_at", "is_active_flag")
    list_filter = ("source", "starts_at")
    search_fields = ("user__username", "reason")
    readonly_fields = ("starts_at",)

    @admin.display(boolean=True, description="Діє")
    def is_active_flag(self, obj):
        return obj.is_active


@admin.register(AdminLog)
class AdminLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("actor", "action", "target", "created_at")
    search_fields = ("actor__username", "action")
    readonly_fields = ("created_at",)
