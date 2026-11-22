"""Маршрути API каталогу."""
from django.urls import path

from .admin_api import (
    AdminProductBlockView,
    AdminProductDeleteView,
    AdminSellerProductsDeleteView,
    AdminProductUnblockView,
    AdminProductsView,
)
from .api_views import (
    CatalogCategoriesView,
    CatalogCategoryPopupView,
    CatalogFiltersView,
    CatalogViewsView,
    ProductDetailView,
    ProductHiddenView,
    ProductListView,
)
from .seller_api import (
    ProductReviewView,
    SellerActivationCodeCheckView,
    SellerDigitalFilesView,
    SellerProductDetailView,
    SellerProductsView,
    SellerVariantPriceView,
    SellerVariantRestockView,
)

urlpatterns = [
    path("catalog/views", CatalogViewsView.as_view(), name="api-catalog-views"),
    path("catalog/categories", CatalogCategoriesView.as_view(), name="api-catalog-categories"),
    path(
        "catalog/categories/<int:pk>/popup",
        CatalogCategoryPopupView.as_view(),
        name="api-catalog-category-popup",
    ),
    path("catalog/filters", CatalogFiltersView.as_view(), name="api-catalog-filters"),
    path("products", ProductListView.as_view(), name="api-products"),
    path("products/mine", SellerProductsView.as_view(), name="api-my-products"),
    path(
        "products/mine/activation-codes/check",
        SellerActivationCodeCheckView.as_view(),
        name="api-my-activation-code-check",
    ),
    path(
        "products/mine/<int:pk>",
        SellerProductDetailView.as_view(),
        name="api-my-product-detail",
    ),
    path(
        "products/mine/<int:pk>/digital-files",
        SellerDigitalFilesView.as_view(),
        name="api-my-product-digital-files",
    ),
    path(
        "products/mine/<int:pk>/variants/<int:variant_id>/restock",
        SellerVariantRestockView.as_view(),
        name="api-my-variant-restock",
    ),
    path(
        "products/mine/<int:pk>/variants/<int:variant_id>/price",
        SellerVariantPriceView.as_view(),
        name="api-my-variant-price",
    ),
    path(
        "products/<int:pk>/reviews",
        ProductReviewView.as_view(),
        name="api-product-reviews",
    ),
    path("products/h/<str:token>", ProductHiddenView.as_view(), name="api-product-hidden"),
    path("products/<int:pk>", ProductDetailView.as_view(), name="api-product-detail"),
    # Адмінська модерація товарів (право «products»)
    path("admin/products", AdminProductsView.as_view(), name="api-admin-products"),
    path(
        "admin/products/sellers/<int:seller_id>/delete-all",
        AdminSellerProductsDeleteView.as_view(),
        name="api-admin-seller-products-delete-all",
    ),
    path(
        "admin/products/<int:pk>/block",
        AdminProductBlockView.as_view(),
        name="api-admin-product-block",
    ),
    path(
        "admin/products/<int:pk>/unblock",
        AdminProductUnblockView.as_view(),
        name="api-admin-product-unblock",
    ),
    path(
        "admin/products/<int:pk>/delete",
        AdminProductDeleteView.as_view(),
        name="api-admin-product-delete",
    ),
]
