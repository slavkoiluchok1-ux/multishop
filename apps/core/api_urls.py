"""Маршрути API ядра."""
from django.urls import path

from .api_views import (
    AdminSiteBlockView,
    AdminSiteBlocksReorderView,
    AdminSiteBlocksView,
    SiteBlocksView,
)

urlpatterns = [
    path("site/blocks", SiteBlocksView.as_view(), name="api-site-blocks"),
    path("admin/site/blocks", AdminSiteBlocksView.as_view(), name="api-admin-site-blocks"),
    path(
        "admin/site/blocks/reorder",
        AdminSiteBlocksReorderView.as_view(),
        name="api-admin-site-blocks-reorder",
    ),
    path("admin/site/blocks/<int:pk>", AdminSiteBlockView.as_view(), name="api-admin-site-block"),
]
