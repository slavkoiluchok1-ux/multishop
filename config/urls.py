"""Головні маршрути MultiShop."""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.static import serve

from apps.core.views import health

urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    # API
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.telegrambot.urls")),
    path("api/", include("apps.policy.urls")),
    path("api/", include("apps.wallet.urls")),
    path("api/", include("apps.core.api_urls")),
    path("api/", include("apps.catalog.api_urls")),
    path("api/", include("apps.cart.urls")),
    path("api/", include("apps.favorites.urls")),
    path("api/", include("apps.notifications.urls")),
    path("api/", include("apps.complaints.urls")),
    path("api/", include("apps.orders.urls")),
    path("api/", include("apps.support.urls")),
    # Фронтенд (той самий домен, щоб сесії й CSRF працювали)
    path("", include("apps.core.urls")),
]

# Віддаємо лише media/public. media/private доступна тільки через захищені view.
urlpatterns += [
    path(
        "media/<path:path>",
        serve,
        {"document_root": settings.PUBLIC_MEDIA_ROOT},
        name="public-media",
    )
]

if settings.DEBUG:
    import debug_toolbar

    urlpatterns += [path("__debug__/", include(debug_toolbar.urls))]
