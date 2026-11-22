"""Маршрути API оформлення та замовлень."""
from django.urls import path

from .views import (
    CancelOrderView,
    CheckoutView,
    ConfirmReceivedView,
    IncomingOrdersView,
    MutualCancelView,
    OrderCommentView,
    OrderDetailView,
    OrdersListView,
    OrderStatusView,
    PaidDownloadView,
    TrialDownloadView,
)

urlpatterns = [
    path("checkout", CheckoutView.as_view(), name="api-checkout"),
    path("orders/mine", OrdersListView.as_view(), name="api-orders-mine"),
    path("orders/incoming", IncomingOrdersView.as_view(), name="api-orders-incoming"),
    path("orders/<int:pk>", OrderDetailView.as_view(), name="api-order-detail"),
    path(
        "orders/<int:pk>/confirm-received",
        ConfirmReceivedView.as_view(),
        name="api-order-confirm-received",
    ),
    path("orders/<int:pk>/status", OrderStatusView.as_view(), name="api-order-status"),
    path("orders/<int:pk>/cancel", CancelOrderView.as_view(), name="api-order-cancel"),
    path(
        "orders/<int:pk>/cancel-agree",
        MutualCancelView.as_view(),
        name="api-order-cancel-agree",
    ),
    path(
        "orders/<int:pk>/comments",
        OrderCommentView.as_view(),
        name="api-order-comments",
    ),
    path("downloads/<int:file_id>", PaidDownloadView.as_view(), name="api-paid-download"),
    path(
        "products/<int:product_id>/trial-files/<int:file_id>",
        TrialDownloadView.as_view(),
        name="api-trial-download",
    ),
]
