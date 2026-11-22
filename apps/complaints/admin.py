from django.contrib import admin

from .models import Complaint, ComplaintComment, ComplaintEvidence, ComplaintType


@admin.register(ComplaintType)
class ComplaintTypeAdmin(admin.ModelAdmin):
    list_display = ("path", "view", "parent", "is_active")
    list_filter = ("view", "is_active")
    search_fields = ("name", "path")
    readonly_fields = ("path",)


class ComplaintEvidenceInline(admin.TabularInline):
    model = ComplaintEvidence
    extra = 0
    readonly_fields = ("created_at",)


class ComplaintCommentInline(admin.TabularInline):
    model = ComplaintComment
    extra = 0
    readonly_fields = ("author", "side", "text", "created_at")
    can_delete = False


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = ("id", "complaint_type", "target_kind", "status", "author", "created_at")
    list_filter = ("status", "target_kind", "complaint_type__view", "created_at")
    search_fields = (
        "id",
        "author__username",
        "target_user__username",
        "text",
        "order__id",
    )
    readonly_fields = ("author", "created_at", "resolved_at")
    inlines = [ComplaintEvidenceInline, ComplaintCommentInline]
