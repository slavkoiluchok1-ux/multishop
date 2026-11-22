"""Маршрути API скарг: довідник типів, подача, перегляд, коментарі, докази."""
from django.urls import path

from .api_views import (
    ComplaintCommentView,
    ComplaintDetailView,
    ComplaintEvidenceView,
    ComplaintListCreateView,
    ComplaintTypesView,
)
from .admin_api import (
    AdminComplaintCommentView,
    AdminComplaintDetailView,
    AdminComplaintResolveView,
    AdminComplaintsView,
    AdminComplaintTakeView,
    AdminLiftComplaintBlockView,
    AdminVerificationView,
    AdminVerificationPhotoView,
)

urlpatterns = [
    path("complaint-types", ComplaintTypesView.as_view(), name="api-complaint-types"),
    path("complaints", ComplaintListCreateView.as_view(), name="api-complaints"),
    path(
        "complaints/evidence/<int:pk>",
        ComplaintEvidenceView.as_view(),
        name="api-complaint-evidence",
    ),
    path("complaints/<int:pk>", ComplaintDetailView.as_view(), name="api-complaint-detail"),
    path(
        "complaints/<int:pk>/comments",
        ComplaintCommentView.as_view(),
        name="api-complaint-comments",
    ),
    # Адмінські маршрути скарг
    path("admin/complaints", AdminComplaintsView.as_view(), name="api-admin-complaints"),
    path(
        "admin/complaints/<int:pk>",
        AdminComplaintDetailView.as_view(),
        name="api-admin-complaint-detail",
    ),
    path(
        "admin/complaints/<int:pk>/take",
        AdminComplaintTakeView.as_view(),
        name="api-admin-complaint-take",
    ),
    path(
        "admin/complaints/<int:pk>/comments",
        AdminComplaintCommentView.as_view(),
        name="api-admin-complaint-comments",
    ),
    path(
        "admin/complaints/<int:pk>/resolve",
        AdminComplaintResolveView.as_view(),
        name="api-admin-complaint-resolve",
    ),
    path(
        "admin/users/<int:pk>/lift-complaint-block",
        AdminLiftComplaintBlockView.as_view(),
        name="api-admin-lift-complaint-block",
    ),
    # Верифікація продавця
    path(
        "admin/verifications/<int:pk>",
        AdminVerificationView.as_view(),
        name="api-admin-verification",
    ),
    path(
        "admin/verifications/<int:pk>/photo",
        AdminVerificationPhotoView.as_view(),
        name="api-admin-verification-photo",
    ),
]
