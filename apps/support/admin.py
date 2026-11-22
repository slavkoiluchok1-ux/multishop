from django.contrib import admin

from .models import AccountRecovery, SupportMessage, SupportTicket


class SupportMessageInline(admin.TabularInline):
    model = SupportMessage
    extra = 0
    readonly_fields = ("sender", "from_side", "text", "attachment", "created_at")
    can_delete = False


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "user", "status", "from_banned", "created_at")
    list_filter = ("status", "from_banned", "created_at")
    search_fields = ("id", "subject", "user__username", "contact_email")
    readonly_fields = ("created_at",)
    inlines = [SupportMessageInline]


@admin.register(AccountRecovery)
class AccountRecoveryAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "target_user", "admin", "contacted_via", "created_at")
    search_fields = ("ticket__id", "target_user__username")
    readonly_fields = ("ticket", "target_user", "admin", "created_at")
