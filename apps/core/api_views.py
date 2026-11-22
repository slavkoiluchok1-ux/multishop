"""API ядра: блоки головної сторінки (публічно й для адмінів із правом «home»)."""
from typing import Any, cast

from django.core.cache import cache
from django.db import transaction
from django.utils.text import slugify
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.admin_api import _log, require_admin, require_right
from apps.accounts.models import CustomUser

from .models import SiteBlock


def block_payload(block: SiteBlock, with_id: bool = False) -> dict[str, Any]:
    """Дані блоку. id віддаємо лише адмінським ендпоінтам."""
    data: dict[str, Any] = {
        "key": block.key,
        "eyebrow": block.eyebrow,
        "title": block.title,
        "text": block.text,
        "layout": block.layout,
        "style": block.style,
        "order": block.order,
    }
    if with_id:
        data["id"] = block.pk
    return data


class SiteBlocksView(APIView):
    """GET /api/site/blocks — блоки головної сторінки для гостей і користувачів."""

    permission_classes = [AllowAny]

    def get(self, request):
        blocks = cache.get_or_set(
            "site:homepage-blocks:v1",
            lambda: [block_payload(block) for block in SiteBlock.objects.all()],
            timeout=300,
        )
        return Response({"blocks": blocks})


def unique_block_key(title: str) -> str:
    """Технічний ключ блоку з заголовка (кирилицю зберігаємо), унікальний."""
    base = slugify(title, allow_unicode=True)[:50] or "block"
    key = base
    n = 2
    while SiteBlock.objects.filter(key=key).exists():
        key = f"{base}-{n}"
        n += 1
    return key


class SiteBlockInputSerializer(serializers.Serializer):
    """Вміст і візуальні параметри блоку головної сторінки."""

    eyebrow = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    title = serializers.CharField(max_length=200, trim_whitespace=True)
    text = serializers.CharField(max_length=2000, trim_whitespace=True)
    layout = serializers.ChoiceField(
        choices=SiteBlock.LAYOUT_CHOICES, required=False, default="standard"
    )
    style = serializers.ChoiceField(  # pyright: ignore[reportAssignmentType]
        choices=SiteBlock.STYLE_CHOICES, required=False, default="paper"
    )


def _admin_or_403(request) -> CustomUser:
    """Перевіряє адміна з правом «home»."""
    actor = cast(CustomUser, request.user)
    require_admin(actor)
    require_right(actor, "home")
    return actor


class AdminSiteBlocksView(APIView):
    """GET/POST /api/admin/site/blocks — список і створення (право «home»)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _admin_or_403(request)
        blocks = SiteBlock.objects.all()
        return Response({"blocks": [block_payload(b, with_id=True) for b in blocks]})

    def post(self, request):
        actor = _admin_or_403(request)
        serializer = SiteBlockInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = cast(dict[str, Any], serializer.validated_data)
        last = SiteBlock.objects.order_by("-order").first()
        next_order = (last.order + 1) if last else 0
        block = SiteBlock.objects.create(
            key=unique_block_key(data["title"]),
            eyebrow=data.get("eyebrow", ""),
            title=data["title"],
            text=data["text"],
            layout=data["layout"],
            style=data["style"],
            order=next_order,
        )
        _log(actor, f'Створив блок головної "{block.title}"')
        return Response({"block": block_payload(block, with_id=True)}, status=201)


class AdminSiteBlockView(APIView):
    """PATCH/DELETE /api/admin/site/blocks/<id> — зміна й видалення (право «home»)."""

    permission_classes = [IsAuthenticated]

    @staticmethod
    def _get_block(pk: int) -> SiteBlock:
        block = SiteBlock.objects.filter(pk=pk).first()
        if block is None:
            raise NotFound("Блок не знайдено.")
        return block

    def patch(self, request, pk):
        actor = _admin_or_403(request)
        block = self._get_block(pk)
        serializer = SiteBlockInputSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = cast(dict[str, Any], serializer.validated_data)
        fields: list[str] = []
        for field in ("eyebrow", "title", "text", "layout", "style"):
            if field in data:
                setattr(block, field, data[field])
                fields.append(field)
        if fields:
            block.save(update_fields=fields)
        _log(actor, f'Змінив блок головної "{block.title}"')
        return Response({"block": block_payload(block, with_id=True)})

    def delete(self, request, pk):
        actor = _admin_or_403(request)
        block = self._get_block(pk)
        title = block.title
        block.delete()
        _log(actor, f'Видалив блок головної "{title}"')
        return Response({"ok": True})


class AdminSiteBlocksReorderView(APIView):
    """POST /api/admin/site/blocks/reorder — новий порядок блоків (право «home»)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = _admin_or_403(request)
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids:
            raise ValidationError({"ids": "Очікується непорожній список ідентифікаторів."})
        try:
            order_ids = [int(item) for item in ids]
        except (TypeError, ValueError) as exc:
            raise ValidationError({"ids": "Ідентифікатори мають бути числами."}) from exc
        if len(set(order_ids)) != len(order_ids):
            raise ValidationError({"ids": "Ідентифікатори не мають повторюватись."})
        with transaction.atomic():
            blocks = {b.pk: b for b in SiteBlock.objects.select_for_update()}
            if set(order_ids) != set(blocks):
                raise ValidationError({"ids": "Список має містити всі блоки головної."})
            for index, block_id in enumerate(order_ids):
                block = blocks[block_id]
                if block.order != index:
                    block.order = index
                    block.save(update_fields=["order"])
        _log(actor, "Змінив порядок блоків головної")
        return Response({"blocks": [block_payload(b, with_id=True) for b in SiteBlock.objects.all()]})
