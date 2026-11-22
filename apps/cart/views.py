"""API кошика: гість тримає кошик у сесії, залогінений — у базі."""
from decimal import Decimal

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import ProductVariant
from apps.catalog.pricing import format_card, format_full

from .models import Cart, CartItem

GUEST_CART_SESSION_KEY = "_multishop_guest_cart_session_key"


def _session_key(request, create=False):
    """Ключ сесії; за потреби створює сесію (лише при записі)."""
    key = request.session.session_key
    if not key and create:
        request.session.create()
        key = request.session.session_key
    return key


def get_cart(request, create=False):
    """Кошик користувача (залогінений) або гостя (за ключем сесії)."""
    if request.user.is_authenticated:
        if create:
            cart, _ = Cart.objects.get_or_create(user=request.user)
            return cart
        return Cart.objects.filter(user=request.user).first()

    key = _session_key(request, create=create)
    if not key:
        return None
    cart = Cart.objects.filter(session_key=key, user__isnull=True).first()
    if cart is None and create:
        cart = Cart.objects.create(session_key=key)
    if cart is not None:
        request.session[GUEST_CART_SESSION_KEY] = cart.session_key
    return cart


def _item_payload(item):
    variant = item.variant
    product = variant.product
    current = variant.price_mc
    price_changed = Decimal(item.price_at_add) != Decimal(current)
    # Прихований товар теж відкривається за посиланням і має купуватись,
    # але заблокований чи видалений адміном — ні
    available = (
        product.status in ("active", "hidden")
        and product.moderation == "ok"
        and variant.stock > 0
    )
    subtotal = current * item.quantity
    if product.main_image:
        image = product.main_image.url
    elif variant.image:
        image = variant.image.url
    else:
        image = None
    return {
        "variant_id": variant.id,
        "product_id": product.id,
        "name": product.name,
        "product_type": product.product_type,
        "sku": variant.sku,
        "image": image,
        "seller": product.seller.username,
        "quantity": item.quantity,
        "stock": variant.stock,
        "price_mc": str(current),
        "price_at_add": str(item.price_at_add),
        "price_card": format_card(current),
        "price_full": format_full(current),
        "price_changed": price_changed,
        "available": available,
        "subtotal": str(subtotal),
        "subtotal_full": format_full(subtotal),
    }


def cart_payload(cart):
    """Вміст кошика для фронтенду."""
    if cart is None:
        return {"items": [], "count": 0, "total": "0", "total_full": "0"}
    items = cart.items.select_related("variant__product__seller", "variant__product__category")
    payload_items = [_item_payload(item) for item in items]
    total = sum((Decimal(row["subtotal"]) for row in payload_items), Decimal("0"))
    count = sum(row["quantity"] for row in payload_items)
    return {
        "items": payload_items,
        "count": count,
        "total": str(total),
        "total_full": format_full(total),
    }


class CartView(APIView):
    """GET /api/cart — вміст кошика."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(cart_payload(get_cart(request)))


class CartItemsView(APIView):
    """POST /api/cart/items — додати/змінити кількість; DELETE — прибрати."""

    permission_classes = [AllowAny]

    def post(self, request):
        variant_id = request.data.get("variant_id")
        try:
            quantity = int(request.data.get("quantity", 1))
        except (TypeError, ValueError):
            return Response({"error": "Некоректна кількість."}, status=status.HTTP_400_BAD_REQUEST)

        variant = (
            ProductVariant.objects.select_related("product", "product__seller")
            .filter(pk=variant_id)
            .first()
        )
        if variant is None:
            return Response({"error": "Варіант не знайдено."}, status=status.HTTP_404_NOT_FOUND)
        if request.user.is_authenticated and variant.product.seller_id == request.user.id:
            return Response({"error": "Не можна додати власний товар."}, status=status.HTTP_400_BAD_REQUEST)

        cart = get_cart(request, create=True)

        if quantity <= 0:
            CartItem.objects.filter(cart=cart, variant=variant).delete()
            return Response(cart_payload(cart))

        if variant.stock <= 0 or variant.product.status == "sold_out":
            return Response({"error": "Товар закінчився."}, status=status.HTTP_400_BAD_REQUEST)
        if variant.product.status not in ("active", "hidden"):
            return Response({"error": "Товар недоступний для купівлі."}, status=status.HTTP_400_BAD_REQUEST)
        if variant.product.moderation != "ok":
            return Response({"error": "Товар недоступний."}, status=status.HTTP_400_BAD_REQUEST)

        # Кількість не більша за залишок
        quantity = min(quantity, variant.stock)
        item, created = CartItem.objects.get_or_create(
            cart=cart,
            variant=variant,
            defaults={"quantity": quantity, "price_at_add": variant.price_mc},
        )
        if not created:
            item.quantity = quantity
            item.save(update_fields=["quantity"])
        return Response(cart_payload(cart))

    def delete(self, request):
        variant_id = request.data.get("variant_id") or request.query_params.get("variant_id")
        cart = get_cart(request)
        if cart is not None and variant_id:
            CartItem.objects.filter(cart=cart, variant_id=variant_id).delete()
        return Response(cart_payload(cart))


class CartMergeView(APIView):
    """POST /api/cart/merge — злити гостьовий кошик із кошиком у базі після входу."""

    permission_classes = [AllowAny]

    def post(self, request):
        if not request.user.is_authenticated:
            return Response({"error": "Потрібен вхід."}, status=status.HTTP_401_UNAUTHORIZED)

        key = request.session.pop(
            GUEST_CART_SESSION_KEY,
            request.session.session_key,
        )
        guest = (
            Cart.objects.filter(session_key=key, user__isnull=True).first() if key else None
        )
        user_cart, _ = Cart.objects.get_or_create(user=request.user)

        if guest and guest.id != user_cart.id:
            with transaction.atomic():
                for item in guest.items.select_related("variant").all():
                    existing = CartItem.objects.filter(cart=user_cart, variant=item.variant).first()
                    if existing:
                        merged = existing.quantity + item.quantity
                        stock = item.variant.stock
                        if stock > 0:
                            merged = min(merged, stock)
                        existing.quantity = merged
                        existing.save(update_fields=["quantity"])
                    else:
                        item.cart = user_cart
                        item.save(update_fields=["cart"])
                guest.delete()

        return Response(cart_payload(user_cart))
