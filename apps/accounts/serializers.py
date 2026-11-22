"""Серіалізатори акаунтів: реєстрація, вхід, користувач, профіль."""
import re
from urllib.parse import urlparse
from uuid import uuid4

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers

from .models import CustomUser

AVATAR_MAX_SIZE = 5 * 1024 * 1024
AVATAR_FORMAT_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


class RegisterSerializer(serializers.Serializer):
    """Реєстрація: нік (унікальний), email (унікальний), пароль із підтвердженням."""

    username = serializers.CharField(max_length=20)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    password2 = serializers.CharField(write_only=True)

    def validate_username(self, value):
        if CustomUser.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Такий нік уже зайнятий.")
        return value

    def validate_email(self, value):
        if CustomUser.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Такий email уже зареєстровано.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError({"password2": "Паролі не збігаються."})
        try:
            password_validation.validate_password(attrs["password"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data):
        validated_data.pop("password2")
        password = validated_data.pop("password")
        user = CustomUser(**validated_data)
        user.set_password(password)
        user.save()
        return user


class UserSerializer(serializers.ModelSerializer):
    """Дані користувача для фронтенду."""

    avatar = serializers.SerializerMethodField()
    is_admin_main = serializers.BooleanField(read_only=True)
    is_admin_1 = serializers.BooleanField(read_only=True)
    is_admin_2 = serializers.BooleanField(read_only=True)

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = CustomUser
        fields = (
            "id",
            "username",
            "email",
            "role",
            "avatar",
            "must_change_password",
            "is_admin_main",
            "is_admin_1",
            "is_admin_2",
        )

    def get_avatar(self, obj):
        if not obj.avatar:
            return None
        request = self.context.get("request")
        url = obj.avatar.url
        return request.build_absolute_uri(url) if request else url


class ProfileUpdateSerializer(serializers.Serializer):
    """Оновлення профілю: нік, email, фото, контакти, адреса, автопідстановка."""

    username = serializers.CharField(max_length=20, required=False)
    email = serializers.EmailField(required=False)
    avatar = serializers.FileField(required=False, allow_null=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    telegram = serializers.CharField(max_length=64, required=False, allow_blank=True)
    instagram = serializers.CharField(max_length=64, required=False, allow_blank=True)
    viber = serializers.CharField(max_length=32, required=False, allow_blank=True)
    facebook = serializers.CharField(max_length=128, required=False, allow_blank=True)
    whatsapp = serializers.CharField(max_length=32, required=False, allow_blank=True)
    delivery_country = serializers.CharField(max_length=64, required=False, allow_blank=True)
    delivery_city = serializers.CharField(max_length=64, required=False, allow_blank=True)
    delivery_street = serializers.CharField(max_length=255, required=False, allow_blank=True)
    delivery_postcode = serializers.CharField(max_length=16, required=False, allow_blank=True)
    recipient_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    autofill_delivery = serializers.BooleanField(required=False)

    def validate_username(self, value):
        user = self.context["user"]
        if CustomUser.objects.filter(username__iexact=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("Такий нік уже зайнятий.")
        return value

    def validate_email(self, value):
        user = self.context["user"]
        if CustomUser.objects.filter(email__iexact=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("Такий email уже зареєстровано.")
        return value

    def validate_phone(self, value):
        if value and not re.fullmatch(r"\+380\d{9}", value):
            raise serializers.ValidationError("Введіть номер у форматі +380XXXXXXXXX.")
        return value

    def validate_telegram(self, value):
        if value and not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", value):
            raise serializers.ValidationError("Введіть Telegram-нік у форматі @nickname.")
        return value

    def validate_facebook(self, value):
        if not value:
            return value
        try:
            URLValidator(schemes=["http", "https"])(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                "Введіть посилання на профіль Facebook."
            ) from exc
        hostname = (urlparse(value).hostname or "").lower()
        if hostname != "facebook.com" and not hostname.endswith(".facebook.com"):
            raise serializers.ValidationError("Посилання має вести на facebook.com.")
        return value

    def validate_avatar(self, value):
        if value.size > AVATAR_MAX_SIZE:
            raise serializers.ValidationError("Файл завеликий (максимум 5 МБ).")

        try:
            value.seek(0)
            with Image.open(value) as image:
                image_format = image.format
                width, height = image.size
                if width > 4000 or height > 4000:
                    raise serializers.ValidationError(
                        "Зображення має бути не більше 4000×4000 пікселів."
                    )
                image.verify()
        except serializers.ValidationError:
            raise
        except (
            Image.DecompressionBombError,
            OSError,
            SyntaxError,
            UnidentifiedImageError,
        ) as exc:
            raise serializers.ValidationError("Файл не є коректним зображенням.") from exc
        finally:
            value.seek(0)

        extension = AVATAR_FORMAT_EXTENSIONS.get(image_format or "")
        if extension is None:
            raise serializers.ValidationError("Дозволені формати: JPG, PNG, WebP.")

        value.name = f"{uuid4().hex}{extension}"
        return value
