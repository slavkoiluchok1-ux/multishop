from django.urls import path

from .views import PolicyAcceptView, PolicyView

urlpatterns = [
    path("policy", PolicyView.as_view(), name="api-policy"),
    path("policy/accept", PolicyAcceptView.as_view(), name="api-policy-accept"),
]
