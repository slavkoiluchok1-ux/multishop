"""API сповіщень: лише лічильники (секції додаються на своїх етапах)."""
from typing import cast

from django.db.models import Count
from django.core.cache import cache
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SECTIONS, Notification

# Секції, які показуємо у навігації на цьому етапі
PUBLIC_SECTIONS = ("orders", "complaints", "favorites", "support")


class CountersView(APIView):
    """GET /api/notifications/counters — червоні цифри над вкладками."""

    permission_classes = [AllowAny]

    def get(self, request):
        counters = {section: 0 for section in PUBLIC_SECTIONS}
        if request.user.is_authenticated:
            rows = cast(
                dict[str, int],
                cache.get_or_set(
                    f"notifications:unread:{request.user.pk}",
                    lambda: {
                        row["section"]: row["count"]
                        for row in (
                            Notification.objects.filter(user=request.user, is_read=False)
                            .values("section")
                            .annotate(count=Count("id"))
                        )
                    },
                    timeout=60,
                ),
            )
            counters.update(rows)

            actor = request.user
            if getattr(actor, "is_staff_admin", False):
                if actor.has_right("complaints"):
                    from apps.complaints.admin_api import open_complaints_count

                    counters["admin_complaints"] = open_complaints_count(actor)
                if actor.has_right("support"):
                    from apps.support.admin_api import open_support_count

                    counters["admin_support"] = open_support_count(actor)
        return Response({"counters": counters})


class AdminBadgesView(APIView):
    """GET /api/admin/badges — лічильники нових скарг і звернень для адміна."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.accounts.admin_api import is_admin1, require_admin
        from apps.accounts.models import CustomUser

        actor = cast(CustomUser, request.user)
        require_admin(actor)

        complaints = 0
        if actor.has_right("complaints"):
            from apps.complaints.admin_api import open_complaints_count

            complaints = open_complaints_count(actor)

        support = 0
        if actor.has_right("support"):
            from apps.support.admin_api import open_support_count

            support = open_support_count(actor)

        return Response(
            {
                "complaints": complaints,
                "support": support,
                "total": complaints + support,
            }
        )


class MarkNotificationsReadView(APIView):
    """POST /api/notifications/read — позначити прочитаною секцію сповіщень."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        section = request.data.get("section")
        if section not in {key for key, _ in SECTIONS}:
            raise serializers.ValidationError({"section": "Невідома секція."})
        updated = Notification.objects.filter(
            user=request.user,
            section=section,
            is_read=False,
        ).update(is_read=True)
        Notification.invalidate_unread_cache(request.user.pk)
        return Response({"updated": updated})
