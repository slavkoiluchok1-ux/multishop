"""Адмінське API модерації товарів: список, блокування, розблокування, видалення.

Право «products»: admin_1 і admin_main мають його автоматично, admin_2 — лише
коли право видане. Блокування оборотне, видалення — м'яке (рядок лишається).
"""
from typing import Any, cast

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.admin_api import _log, is_admin1, is_main, require_admin, require_right
from apps.accounts.models import CustomUser
from apps.notifications.services import notify
from apps.wallet.services import recalculate_seller_deposit

from .models import Product

PAGE_SIZE = 20
MIN_REASON_LEN = 5


def _reason_or_error(raw: Any) -> str:
    """Причина обов'язкова й не коротша за 5 символів."""
    reason = str(raw or "").strip()
    if len(reason) < MIN_REASON_LEN:
        raise ValidationError({"reason": "Причина обов'язкова (мінімум 5 символів)."})
    return reason


def _product_payload(product: Product) -> dict[str, Any]:
    return {
        "id": product.id,
        "name": product.name,
        "seller_id": product.seller_id,
        "seller": product.seller.username,
        "status": product.status,
        "status_display": product.get_status_display(),  # pyright: ignore[reportAttributeAccessIssue]
        "moderation": product.moderation,
        "moderation_display": product.get_moderation_display(),  # pyright: ignore[reportAttributeAccessIssue]
        "moderation_reason": product.moderation_reason,
        "moderated_by": product.moderated_by.username if product.moderated_by else None,
        "moderated_at": product.moderated_at,
        "created_at": product.created_at,
    }


def _get_product(pk: int) -> Product:
    product = Product.objects.select_related("seller").filter(pk=pk).first()
    if product is None:
        raise NotFound("Товар не знайдено.")
    return product


def _require_target_allowed(actor: CustomUser, product: Product) -> None:
    """Товари головного адміна модерує лише головний адмін."""
    seller = product.seller
    if getattr(seller, "is_admin_main", False) and not is_main(actor):
        raise PermissionDenied("Товари головного адміна може модерувати лише головний адмін.")


def _notify_seller(product: Product, text: str) -> None:
    notify(
        product.seller,
        "products",
        "moderation",
        text,
        {"product_id": product.id, "moderation": product.moderation},
        url=f"/pages/seller.html?product={product.id}",
    )


def _apply_moderation(actor: CustomUser, product: Product, moderation: str, reason: str) -> None:
    """Записує стан модерації та повертає заставу без блокування нової."""
    product.moderation = moderation
    product.moderation_reason = reason
    product.moderated_by = actor
    product.moderated_at = timezone.now()
    product.save(
        update_fields=["moderation", "moderation_reason", "moderated_by", "moderated_at"]
    )
    # Заблоковані та видалені товари в заставу не входять: перерахунок лише
    # повертає частину застави за правилами 14 днів, ніколи не блокує дію.
    recalculate_seller_deposit(product.seller, order=None, allow_increase=False)


class AdminProductsView(APIView):
    """GET /api/admin/products?q=&status=&page= — список товарів (право «products»)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        require_right(actor, "products")

        query = str(request.query_params.get("q") or "").strip()
        status_filter = str(request.query_params.get("status") or "all")
        # Адмін бачить усі товари, зокрема hidden та inactive
        products = Product.objects.select_related("seller").order_by("-created_at")
        if query:
            condition = Q(name__icontains=query) | Q(seller__username__icontains=query)
            if query.isdigit():
                condition |= Q(id=int(query))
            products = products.filter(condition)
        if status_filter in ("ok", "blocked", "deleted"):
            products = products.filter(moderation=status_filter)

        paginator = Paginator(products, PAGE_SIZE)
        page = paginator.get_page(request.query_params.get("page"))
        return Response(
            {
                "count": paginator.count,
                "page": page.number,
                "pages": paginator.num_pages,
                # Видалені товари відновлює лише адмін 1-го рівня або головний
                "can_restore": is_admin1(actor),
                "results": [_product_payload(product) for product in page.object_list],
            }
        )


class AdminProductBlockView(APIView):
    """POST /api/admin/products/{id}/block — заблокувати товар (право «products»)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        require_right(actor, "products")
        product = _get_product(pk)
        _require_target_allowed(actor, product)
        reason = _reason_or_error(request.data.get("reason"))
        with transaction.atomic():
            _apply_moderation(actor, product, "blocked", reason)
            _log(
                actor,
                f'Заблокував товар "{product.name}" (#{product.id}): {reason}',
                product.seller,
            )
            _notify_seller(
                product,
                f"Товар «{product.name}» заблоковано адміністратором. Причина: {reason}",
            )
        return Response({"product": _product_payload(product)})


class AdminProductUnblockView(APIView):
    """POST /api/admin/products/{id}/unblock — зняти блокування (право «products»)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        require_right(actor, "products")
        product = _get_product(pk)
        _require_target_allowed(actor, product)
        # Видалений товар відновлює лише адмін 1-го рівня або головний
        if product.moderation == "deleted" and not is_admin1(actor):
            raise PermissionDenied(
                "Відновити видалений товар може лише адмін 1-го рівня або головний."
            )
        with transaction.atomic():
            _apply_moderation(actor, product, "ok", "")
            _log(actor, f'Розблокував товар "{product.name}" (#{product.id})', product.seller)
            _notify_seller(product, f"Товар «{product.name}» розблоковано адміністратором.")
        return Response({"product": _product_payload(product)})


class AdminProductDeleteView(APIView):
    """POST /api/admin/products/{id}/delete — м'яке видалення (право «products»)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        require_right(actor, "products")
        product = _get_product(pk)
        _require_target_allowed(actor, product)
        reason = _reason_or_error(request.data.get("reason"))
        with transaction.atomic():
            _apply_moderation(actor, product, "deleted", reason)
            _log(
                actor,
                f'Видалив товар "{product.name}" (#{product.id}): {reason}',
                product.seller,
            )
            _notify_seller(
                product,
                f"Товар «{product.name}» видалено адміністратором. Причина: {reason}",
            )
        return Response({"product": _product_payload(product)})


class AdminSellerProductsDeleteView(APIView):
    """POST /api/admin/products/sellers/{id}/delete-all — м'яко видалити товари продавця."""

    permission_classes = [IsAuthenticated]

    def post(self, request, seller_id):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        require_right(actor, "products")
        seller = CustomUser.objects.filter(pk=seller_id).first()
        if seller is None:
            raise NotFound("Продавця не знайдено.")
        if getattr(seller, "is_admin_main", False) and not is_main(actor):
            raise PermissionDenied("Товари головного адміна може модерувати лише головний адмін.")
        reason = _reason_or_error(request.data.get("reason"))
        products = list(
            Product.objects.filter(seller=seller)
            .exclude(moderation="deleted")
            .order_by("id")
        )
        if not products:
            return Response({"seller": seller.username, "deleted_count": 0})

        moderated_at = timezone.now()
        for product in products:
            product.moderation = "deleted"
            product.moderation_reason = reason
            product.moderated_by = actor
            product.moderated_at = moderated_at

        with transaction.atomic():
            Product.objects.bulk_update(
                products,
                ["moderation", "moderation_reason", "moderated_by", "moderated_at"],
            )
            recalculate_seller_deposit(seller, order=None, allow_increase=False)
            _log(
                actor,
                f"Видалив усі товари продавця #{seller.id} "
                f"({len(products)} шт.): {reason[:180]}",
                seller,
            )
            notify(
                seller,
                "products",
                "products_deleted",
                f"Адміністратор видалив усі ваші товари ({len(products)}). Причина: {reason[:150]}",
                {"seller_id": seller.id, "deleted_count": len(products)},
                url="/pages/seller.html",
            )
            from apps.notifications.signals import notify_favorites_about_moderated_product

            for product in products:
                notify_favorites_about_moderated_product(
                    product,
                    "видалено адміністратором",
                )

        return Response({"seller": seller.username, "deleted_count": len(products)})
