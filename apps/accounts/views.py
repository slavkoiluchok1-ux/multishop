"""API акаунтів: реєстрація, вхід, вихід, поточний користувач, профіль."""
from pathlib import Path
from typing import Any, cast

from django.conf import settings
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.policy.services import has_accepted_current
from apps.core.throttles import (
    LoginRateThrottle,
    RegistrationRateThrottle,
    UploadRateThrottle,
)

from .models import AdminRight, Ban, CustomUser, SellerVerification
from .serializers import ProfileUpdateSerializer, RegisterSerializer, UserSerializer


def _delete_avatar_file(storage, name: str) -> None:
    """Видаляє лише файл, фізично розміщений у публічній папці аватарів."""
    if not name:
        return
    try:
        avatar_root = (Path(settings.PUBLIC_MEDIA_ROOT) / "avatars").resolve()
        stored_path = Path(storage.path(name)).resolve()
        stored_path.relative_to(avatar_root)
    except (NotImplementedError, OSError, ValueError):
        return
    if stored_path != avatar_root:
        storage.delete(name)


def user_payload(user: CustomUser, request) -> dict:
    """Дані користувача для фронтенду."""
    data: dict[str, Any] = dict(UserSerializer(user, context={"request": request}).data)
    data["authenticated"] = True
    data["policy_accepted"] = has_accepted_current(user)
    verification = SellerVerification.objects.filter(user=user).only("age", "is_locked").first()
    data["seller_verified"] = bool(verification and verification.age >= 18)
    data["verified_age"] = verification.age if verification and verification.age >= 18 else None
    data["seller_verification_age"] = verification.age if verification else None
    active_ban = (
        Ban.objects.active()
        .filter(user=user)
        .select_related("issued_by")
        .order_by("-starts_at")
        .first()
    )
    data["is_banned"] = active_ban is not None
    data["active_ban"] = (
        {
            "id": active_ban.pk,
            "reason": active_ban.reason,
            "starts_at": active_ban.starts_at,
            "ends_at": active_ban.ends_at,
            "issued_by": active_ban.issued_by.username if active_ban.issued_by else "Система",
        }
        if active_ban is not None
        else None
    )
    # Права адміна для навігації: у головного й 1-го рівня — усі
    if user.is_staff_admin:
        if user.is_admin_main or user.is_admin_1:
            data["admin_rights"] = [key for key, _ in AdminRight.RIGHTS]
        else:
            data["admin_rights"] = list(
                AdminRight.objects.filter(user=user).values_list("right", flat=True)
            )
    else:
        data["admin_rights"] = []
    return data


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """GET /api/auth/csrf — встановлює cookie csrftoken для фронтенду."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"ok": True})


class RegisterView(APIView):
    """POST /api/auth/register — реєстрація з автологіном."""

    permission_classes = [AllowAny]
    throttle_classes = [RegistrationRateThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = cast(CustomUser, serializer.save())
        login(request, user)
        return Response(user_payload(user, request), status=status.HTTP_201_CREATED)


class LoginView(APIView):
    """POST /api/auth/login — вхід за ніком або email."""

    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        login_value = request.data.get("login") or request.data.get("username") or ""
        password = request.data.get("password") or ""
        user = CustomUser.find_by_login(login_value)
        if user is None or not user.check_password(password):
            return Response({"error": "Невірний нік/email або пароль."}, status=status.HTTP_400_BAD_REQUEST)
        if not user.is_active:
            return Response({"error": "Акаунт вимкнено."}, status=status.HTTP_403_FORBIDDEN)
        login(request, user)
        return Response(user_payload(user, request))


class LogoutView(APIView):
    """POST /api/auth/logout."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response({"ok": True})


class MeView(APIView):
    """GET /api/auth/me — поточний користувач або гість."""

    permission_classes = [AllowAny]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"authenticated": False, "policy_accepted": False})
        user = cast(CustomUser, request.user)
        return Response(user_payload(user, request))


class UsernameCheckView(APIView):
    """GET /api/auth/username-check?username=xxx — чи існує такий нік."""

    permission_classes = [AllowAny]

    def get(self, request):
        username = (request.query_params.get("username") or "").strip()
        if not username:
            return Response({"exists": False})
        exists = CustomUser.objects.filter(username__iexact=username).exists()
        return Response({"exists": exists})


class ProfileView(APIView):
    """GET/PUT/PATCH /api/profile — профіль користувача."""

    permission_classes = [IsAuthenticated]

    def _payload(self, user: CustomUser, request):
        profile = user.profile
        return {
            **user_payload(user, request),
            "profile": {
                "phone": profile.phone,
                "telegram": profile.telegram,
                "instagram": profile.instagram,
                "viber": profile.viber,
                "facebook": profile.facebook,
                "whatsapp": profile.whatsapp,
                "delivery_country": profile.delivery_country,
                "delivery_city": profile.delivery_city,
                "delivery_street": profile.delivery_street,
                "delivery_postcode": profile.delivery_postcode,
                "recipient_name": profile.recipient_name,
                "autofill_delivery": profile.autofill_delivery,
            },
        }

    def get(self, request):
        user = cast(CustomUser, request.user)
        return Response(self._payload(user, request))

    def _update(self, request, partial: bool = False):
        user = cast(CustomUser, request.user)
        serializer = ProfileUpdateSerializer(
            data=request.data,
            context={"user": user},
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        data = cast(dict[str, Any], serializer.validated_data)

        user_fields = []
        old_avatar_name = user.avatar.name or ""
        new_avatar_name = ""
        if "username" in data:
            user.username = data["username"]
            user_fields.append("username")
        if "email" in data:
            user.email = data["email"]
            user_fields.append("email")
        if "avatar" in data and data["avatar"] is not None:
            user.avatar.save(data["avatar"].name, data["avatar"], save=False)
            new_avatar_name = user.avatar.name
            user_fields.append("avatar")
        if user_fields:
            if new_avatar_name:
                try:
                    user.save(update_fields=user_fields)
                except Exception:
                    _delete_avatar_file(user.avatar.storage, new_avatar_name)
                    user.avatar.name = old_avatar_name
                    raise
                if old_avatar_name and old_avatar_name != new_avatar_name:
                    _delete_avatar_file(user.avatar.storage, old_avatar_name)
            else:
                user.save(update_fields=user_fields)

        profile = user.profile
        for field in (
            "phone",
            "telegram",
            "instagram",
            "viber",
            "facebook",
            "whatsapp",
            "delivery_country",
            "delivery_city",
            "delivery_street",
            "delivery_postcode",
            "recipient_name",
            "autofill_delivery",
        ):
            if field in data:
                setattr(profile, field, data[field])
        profile.save()

        return Response(self._payload(user, request))

    def put(self, request):
        return self._update(request)

    def patch(self, request):
        return self._update(request, partial=True)


class ProfileAvatarDeleteView(APIView):
    """DELETE /api/profile/avatar — видаляє фото лише поточного користувача."""

    permission_classes = [IsAuthenticated]

    def delete(self, request):
        user = cast(CustomUser, request.user)
        avatar_name = user.avatar.name or ""
        if avatar_name:
            storage = user.avatar.storage
            user.avatar = None
            user.save(update_fields=["avatar"])
            _delete_avatar_file(storage, avatar_name)
        return Response(user_payload(user, request))


class SellerVerificationView(APIView):
    """GET/POST /api/profile/seller-verification — одноразова перевірка продавця."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadRateThrottle]

    class SubmissionSerializer(serializers.Serializer):
        photo = serializers.ImageField()
        age = serializers.IntegerField(min_value=18, max_value=120)

    def get(self, request):
        user = cast(CustomUser, request.user)
        verification = SellerVerification.objects.filter(user=user).first()
        if verification is None:
            return Response({"verified": False})
        return Response(
            {
                "verified": verification.age >= 18,
                "age": verification.age,
                "is_locked": verification.is_locked,
                "created_at": verification.created_at,
            }
        )

    def post(self, request):
        user = cast(CustomUser, request.user)
        if SellerVerification.objects.filter(user=user).exists():
            raise serializers.ValidationError(
                {"verification": "Верифікацію вже подано, змінити її неможливо."}
            )
        serializer = self.SubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = cast(dict[str, Any], serializer.validated_data)
        verification = SellerVerification.objects.create(
            user=user,
            photo=data["photo"],
            age=data["age"],
            is_locked=True,
        )
        return Response(
            {
                "verified": True,
                "age": verification.age,
                "is_locked": verification.is_locked,
                "created_at": verification.created_at,
            },
            status=status.HTTP_201_CREATED,
        )


class PasswordChangeView(APIView):
    """POST /api/profile/password — зміна паролю.

    Звичайний сценарій: старий пароль + новий двічі.
    Якщо у користувача must_change_password=True (тимчасовий пароль від підтримки),
    старий пароль не потрібен — лише новий двічі для підтвердження.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = cast(CustomUser, request.user)
        old_password = request.data.get("old_password") or ""
        new_password = request.data.get("new_password") or ""
        new_password2 = request.data.get("new_password2") or ""

        # Старий пароль потрібен лише коли користувач сам міняє пароль,
        # а не обмушений змінити тимчасовий.
        if not user.must_change_password:
            if not old_password:
                raise ValidationError({"old_password": "Введіть поточний пароль."})
            if not user.check_password(old_password):
                raise ValidationError({"old_password": "Поточний пароль невірний."})

        if not new_password:
            raise ValidationError({"new_password": "Введіть новий пароль."})
        if new_password != new_password2:
            raise ValidationError({"new_password2": "Паролі не збігаються."})
        try:
            password_validation.validate_password(new_password, user)
        except DjangoValidationError as exc:
            raise ValidationError({"new_password": list(exc.messages)})

        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        # Залишаємо користувача залогіненим після зміни паролю.
        update_session_auth_hash(request, user)
        return Response({"ok": True})
