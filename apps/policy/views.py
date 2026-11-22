"""API політики."""
from typing import Any, cast

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PolicyAcceptance
from .serializers import PolicyAcceptanceSerializer, PolicyVersionSerializer
from .services import accepted_version, current_version


class PolicyView(APIView):
    """GET /api/policy — поточна редакція (публічно) і стан прийняття."""

    permission_classes = [AllowAny]

    def get(self, request):
        version = current_version()
        if version is None:
            return Response({"number": None, "text": "", "accepted": False})

        data = cast(dict[str, Any], PolicyVersionSerializer(version).data)
        accepted = False
        accepted_at = None
        if request.user.is_authenticated:
            acceptance = PolicyAcceptance.objects.filter(
                user=request.user, version=version
            ).first()
            accepted = acceptance is not None
            accepted_at = acceptance.accepted_at if acceptance else None

        data["accepted"] = accepted
        data["accepted_at"] = accepted_at
        current_acceptance = (
            accepted_version(request.user) if request.user.is_authenticated else None
        )
        data["accepted_version"] = (
            current_acceptance.number if current_acceptance is not None else None
        )
        return Response(data)


class PolicyAcceptView(APIView):
    """POST /api/policy/accept — прийняти поточну редакцію."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        version = current_version()
        if version is None:
            return Response({"error": "Політику не опубліковано"}, status=status.HTTP_400_BAD_REQUEST)

        acceptance, created = PolicyAcceptance.objects.get_or_create(
            user=request.user, version=version
        )
        if not created:
            acceptance.accepted_at = timezone.now()
            acceptance.save(update_fields=["accepted_at"])
        return Response(PolicyAcceptanceSerializer({
            "version": version.number,
            "accepted_at": acceptance.accepted_at,
        }).data)
