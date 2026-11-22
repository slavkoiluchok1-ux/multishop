"""API обраного: список, додавання/прибирання, сповіщення про зміни."""
from django.db.models import Min
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Product
from apps.catalog.pricing import format_card, format_full
from .models import Favorite


def _current_price(product):
    return product.variants.aggregate(m=Min("price_mc"))["m"]


class FavoritesView(APIView):
    """GET/POST/DELETE /api/favorites — обране користувача."""

    permission_classes = [AllowAny]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"authenticated": False, "results": []})

        favorites = (
            Favorite.objects.filter(user=request.user)
            .select_related("product__seller", "product__category")
            .prefetch_related("product__variants")
        )
        results = []
        for favorite in favorites:
            product = favorite.product
            price = min((v.price_mc for v in product.variants.all()), default=None)
            self._notify(favorite, product, price)
            results.append(
                {
                    "product_id": product.id,
                    "name": product.name,
                    "slug": product.slug,
                    "image": product.main_image.url if product.main_image else None,
                    "status": product.status,
                    # Заблокований чи видалений адміном товар у списку недоступний
                    "moderation": product.moderation,
                    "available": product.moderation == "ok",
                    "price_mc": str(price) if price is not None else "0",
                    "price_card": format_card(price),
                    "price_full": format_full(price),
                    "seller": product.seller.username,
                    "category_path": product.category.path,
                }
            )

        # Червона цифра «Обране» зникає після перегляду списку
        from apps.notifications.models import Notification

        Notification.objects.filter(
            user=request.user, section="favorites", is_read=False
        ).update(is_read=True)

        return Response({"authenticated": True, "results": results})

    def _notify(self, favorite, product, price):
        """Синхронізує стан; подієві сповіщення надсилаються сигналами моделей."""
        Favorite.objects.filter(pk=favorite.pk).update(
            last_price=price,
            last_status=product.status,
        )

    def post(self, request):
        if not request.user.is_authenticated:
            return Response({"error": "Потрібен вхід."}, status=status.HTTP_401_UNAUTHORIZED)

        product_id = request.data.get("product_id")
        product = Product.objects.filter(pk=product_id).first()
        if product is None:
            return Response({"error": "Товар не знайдено."}, status=status.HTTP_404_NOT_FOUND)

        favorite = Favorite.objects.filter(user=request.user, product=product).first()
        if favorite:
            favorite.delete()
            is_favorite = False
        else:
            Favorite.objects.create(
                user=request.user,
                product=product,
                last_price=_current_price(product),
                last_status=product.status,
            )
            is_favorite = True
        return Response({"is_favorite": is_favorite})

    def delete(self, request):
        if not request.user.is_authenticated:
            return Response({"error": "Потрібен вхід."}, status=status.HTTP_401_UNAUTHORIZED)
        product_id = request.data.get("product_id") or request.query_params.get("product_id")
        Favorite.objects.filter(user=request.user, product_id=product_id).delete()
        return Response({"is_favorite": False})
