"""Маршрути ядра: фронтенд віддається з того ж домену."""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="frontend-home"),
    # Прихований товар відкривається будь-кому за посиланням
    path("catalog/h/<str:token>", views.product_hidden, name="frontend-product-hidden"),
    path("pages/<path:name>", views.page, name="frontend-page"),
]
