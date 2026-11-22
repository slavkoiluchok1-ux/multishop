"""Маршрути API сповіщень."""
from django.urls import path

from .views import AdminBadgesView, CountersView, MarkNotificationsReadView

urlpatterns = [
    path("admin/badges", AdminBadgesView.as_view(), name="api-admin-badges"),
    path("notifications/counters", CountersView.as_view(), name="api-notifications-counters"),
    path("notifications/read", MarkNotificationsReadView.as_view(), name="api-notifications-read"),
]
