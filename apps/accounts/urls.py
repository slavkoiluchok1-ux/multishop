"""Маршрути API акаунтів."""
from django.urls import path

from .admin_api import (
    AdminBanLiftView,
    AdminBansView,
    AdminCabinetView,
    AdminCoinIssueView,
    AdminUserSearchView,
    AdminUserView,
)
from .views import (
    CsrfView,
    LoginView,
    LogoutView,
    MeView,
    PasswordChangeView,
    ProfileAvatarDeleteView,
    ProfileView,
    RegisterView,
    SellerVerificationView,
    UsernameCheckView,
)

urlpatterns = [
    path("auth/csrf", CsrfView.as_view(), name="api-auth-csrf"),
    path("auth/register", RegisterView.as_view(), name="api-auth-register"),
    path("auth/login", LoginView.as_view(), name="api-auth-login"),
    path("auth/logout", LogoutView.as_view(), name="api-auth-logout"),
    path("auth/me", MeView.as_view(), name="api-auth-me"),
    path("auth/username-check", UsernameCheckView.as_view(), name="api-auth-username-check"),
    path("profile", ProfileView.as_view(), name="api-profile"),
    path("profile/avatar", ProfileAvatarDeleteView.as_view(), name="api-profile-avatar"),
    path(
        "profile/password",
        PasswordChangeView.as_view(),
        name="api-profile-password",
    ),
    path(
        "profile/seller-verification",
        SellerVerificationView.as_view(),
        name="api-seller-verification",
    ),
    # Адмінські сторінки (розділ 7)
    path("admin/cabinet", AdminCabinetView.as_view(), name="api-admin-cabinet"),
    path("admin/users/search", AdminUserSearchView.as_view(), name="api-admin-users-search"),
    path("admin/users/<int:pk>", AdminUserView.as_view(), name="api-admin-user"),
    path("admin/bans", AdminBansView.as_view(), name="api-admin-bans"),
    path("admin/bans/<int:pk>/lift", AdminBanLiftView.as_view(), name="api-admin-ban-lift"),
    path("admin/coins/issue", AdminCoinIssueView.as_view(), name="api-admin-coins-issue"),
]
