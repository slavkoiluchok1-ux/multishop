"""Маршрути API кошика."""
from django.urls import path

from .views import CartItemsView, CartMergeView, CartView

urlpatterns = [
    path("cart", CartView.as_view(), name="api-cart"),
    path("cart/items", CartItemsView.as_view(), name="api-cart-items"),
    path("cart/merge", CartMergeView.as_view(), name="api-cart-merge"),
]
