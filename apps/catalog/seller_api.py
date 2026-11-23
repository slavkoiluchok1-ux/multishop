"""API продавця: власні товари, варіанти й приватні цифрові файли."""
import json
from decimal import Decimal
from typing import Any, cast

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import CustomUser, SellerVerification
from apps.catalog.api_views import _can_view
from apps.catalog.category_data import (
    DELIVERY,
    is_one_time_development_path,
    product_type_for_path,
    uses_activation_codes,
    uses_digital_delivery,
)
from apps.catalog.models import (
    Attribute,
    Category,
    DigitalFile,
    MAX_PRICE_MC,
    Product,
    ProductVariant,
    PRODUCT_STATUSES,
    PRODUCT_TYPES,
    Review,
    ReviewMedia,
    VariantAttributeValue,
    sync_stock_status,
)
from apps.core.models import SiteSettings
from apps.core.storage import validate_image, validate_upload
from apps.core.throttles import (
    ActivationCodeCheckThrottle,
    ReviewRateThrottle,
    UploadRateThrottle,
)
from apps.orders.models import (
    ActivationCode,
    DownloadGrant,
    Order,
    OrderItem,
    activation_code_key,
)
from apps.wallet.models import SellerDeposit, Wallet
from apps.wallet.services import charge_hidden_listing_fee, recalculate_seller_deposit


class VariantInputSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False)
    sku = serializers.CharField(max_length=64, required=False, allow_blank=True)
    price_mc = serializers.DecimalField(
        max_digits=15,
        decimal_places=6,
        min_value=Decimal("0.000001"),
        max_value=MAX_PRICE_MC,
    )
    stock = serializers.IntegerField(min_value=0)
    license_type = serializers.ChoiceField(
        choices=ProductVariant.LICENSE_CHOICES, required=False, allow_blank=True
    )
    attributes = serializers.ListField(
        child=serializers.DictField(), required=False, allow_empty=True
    )
    activation_codes = serializers.ListField(
        child=serializers.CharField(max_length=256, trim_whitespace=True),
        required=False,
        allow_empty=True,
        max_length=1000,
    )


class ActivationCodeCheckInputSerializer(serializers.Serializer):
    codes = serializers.ListField(
        child=serializers.CharField(max_length=256, trim_whitespace=True),
        allow_empty=True,
        max_length=1000,
    )


class VariantRestockInputSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, max_value=2147483647)
    activation_codes = serializers.ListField(
        child=serializers.CharField(max_length=256, trim_whitespace=True),
        required=False,
        allow_empty=True,
        max_length=1000,
    )


class VariantPriceInputSerializer(serializers.Serializer):
    price_mc = serializers.DecimalField(
        max_digits=15,
        decimal_places=6,
        min_value=Decimal("0.000001"),
        max_value=MAX_PRICE_MC,
    )


class ProductInputSerializer(serializers.Serializer):
    category_id = serializers.IntegerField()
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    brand = serializers.CharField(max_length=120, required=False, allow_blank=True)
    portfolio_url = serializers.URLField(
        required=False,
        allow_blank=True,
        max_length=500,
    )
    main_image = serializers.ImageField(required=False, allow_null=True)
    product_type = serializers.ChoiceField(choices=PRODUCT_TYPES, required=False)
    status = serializers.ChoiceField(choices=PRODUCT_STATUSES, required=False)
    variants = VariantInputSerializer(many=True, allow_empty=False)

    def validate_main_image(self, value):
        try:
            validate_image(value)
        except DjangoValidationError as exc:
            message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            raise serializers.ValidationError(message) from exc
        return value

    def validate_portfolio_url(self, value):
        if value and not value.lower().startswith(("http://", "https://")):
            raise serializers.ValidationError(
                "Посилання на портфоліо має починатися з http:// або https://."
            )
        return value

    def validate_category_id(self, value):
        category = Category.objects.filter(pk=value, is_active=True).first()
        if category is None:
            raise serializers.ValidationError("Оберіть активну категорію.")
        if Category.objects.filter(parent=category, is_active=True).exists():
            raise serializers.ValidationError("Товар можна додати лише до кінцевої категорії.")
        return value

    def validate(self, attrs):
        product = self.context.get("product")
        category_id = attrs.get("category_id")
        if category_id is None and product is not None:
            category_id = product.category_id
        if category_id is None:
            raise serializers.ValidationError({"category_id": "Оберіть категорію товару."})
        category = Category.objects.only("path", "name").get(pk=category_id)
        path = category.path or category.name
        derived_type = product_type_for_path(path)
        if derived_type:
            # Тип визначає гілка категорії; чуже значення від клієнта відхиляємо
            client_type = attrs.get("product_type")
            if client_type and client_type != derived_type:
                raise serializers.ValidationError(
                    {"product_type": "Тип товару визначає обрана категорія."}
                )
            if (
                product is not None
                and category_id != product.category_id
                and derived_type != product.product_type
            ):
                raise serializers.ValidationError({
                    "category_id": "Категорію можна змінити лише в межах одного типу товару."
                })
            attrs["product_type"] = derived_type
        elif "product_type" not in attrs and product is not None:
            attrs["product_type"] = product.product_type

        delivery_attribute = _delivery_attribute(category)
        if (
            delivery_attribute
            and attrs.get("product_type") in ("digital_key", "subscription")
            and "variants" in attrs
        ):
            code_keys = set()
            for index, variant in enumerate(attrs["variants"], start=1):
                delivery_values = [
                    str(item.get("value") or "").strip()
                    for item in variant.get("attributes", [])
                    if str(item.get("attribute_id") or "") == str(delivery_attribute.id)
                ]
                if any(value not in DELIVERY for value in delivery_values):
                    raise serializers.ValidationError({
                        "variants": (
                            f"Варіант {index}: оберіть підтримуваний спосіб видачі."
                        )
                    })
                is_code_delivery = uses_activation_codes(
                    attrs["product_type"], delivery_values
                )
                codes = variant.get("activation_codes", [])
                if attrs["product_type"] == "subscription" and not delivery_values:
                    raise serializers.ValidationError({
                        "variants": f"Варіант {index}: оберіть спосіб видачі підписки."
                    })
                if not is_code_delivery:
                    if codes:
                        raise serializers.ValidationError({
                            "variants": f"Варіант {index}: коди можна додавати лише для видачі за кодом."
                        })
                    continue
                if len(codes) != variant["stock"]:
                    raise serializers.ValidationError({
                        "variants": (
                            f"Варіант {index}: додайте рівно {variant['stock']} "
                            "унікальних кодів — по одному на кожну одиницю."
                        )
                    })
                for code in codes:
                    key = activation_code_key(code)
                    if key in code_keys:
                        raise serializers.ValidationError({
                            "variants": "Один і той самий код не можна додати кілька разів."
                        })
                    code_keys.add(key)

            existing_keys = set(
                ActivationCode.objects.filter(code_key__in=code_keys)
                .values_list("code_key", flat=True)
            )
            if existing_keys:
                raise serializers.ValidationError({
                    "variants": "Один або кілька кодів уже використані в іншому оголошенні."
                })
        if is_one_time_development_path(path):
            if (
                product is not None
                and "variants" in attrs
                and OrderItem.objects.filter(variant__product=product).exists()
            ):
                raise serializers.ValidationError({
                    "variants": "Цю готову розробку вже придбали; повторно виставити її не можна."
                })
            if "variants" in attrs and (
                len(attrs["variants"]) != 1
                or attrs["variants"][0]["stock"] != 1
            ):
                raise serializers.ValidationError({
                    "variants": "Готову розробку можна продати один раз: один варіант і залишок 1."
                })
        return attrs

    def validate_variants(self, variants):
        if not variants:
            raise serializers.ValidationError("Додайте хоча б один варіант товару.")
        return variants


def _delivery_attribute(category: Category) -> Attribute | None:
    current = category
    while current is not None:
        attribute = Attribute.objects.filter(category=current, name="Видача").first()
        if attribute is not None:
            return attribute
        current = current.parent
    return None


def _seller_product_queryset():
    return Product.objects.select_related("category").prefetch_related(
        "variants__attribute_values__attribute", "digital_files"
    )


def _seller_product_payload(
    product: Product, sold_variant_ids: set[int] | None = None
):
    if sold_variant_ids is None:
        sold_variant_ids = set(
            OrderItem.objects.filter(variant__product=product).values_list(
                "variant_id", flat=True
            )
        )
    return {
        "id": product.id,
        "name": product.name,
        "description": product.description,
        "brand": product.brand,
        "portfolio_url": product.portfolio_url,
        "category_id": product.category_id,
        "category_path": product.category.path,
        "main_image": product.main_image.url if product.main_image else None,
        "product_type": product.product_type,
        "status": product.status,
        "moderation": product.moderation,
        "moderation_reason": product.moderation_reason,
        "can_restock": (
            product.status in ("active", "hidden", "sold_out")
            and product.moderation == "ok"
            and not is_one_time_development_path(product.category.path)
        ),
        "hidden_url": (
            f"/catalog/h/{product.hidden_token}" if product.status == "hidden" else None
        ),
        "variants": [
            {
                "id": variant.id,
                "sku": variant.sku,
                "price_mc": str(variant.price_mc),
                "stock": variant.stock,
                "license_type": variant.license_type,
                "can_change_price": variant.id not in sold_variant_ids,
                "requires_activation_codes": _variant_uses_activation_codes(variant),
                "attributes": [
                    {"attribute_id": value.attribute_id, "value": value.value}
                    for value in variant.attribute_values.all()
                ],
            }
            for variant in product.variants.all()
        ],
        "digital_files": [
            {
                "id": file_record.id,
                "title": file_record.title or (file_record.file.name or "").rsplit("/", 1)[-1],
                "variant_id": file_record.variant_id,
                "is_trial": file_record.is_trial,
            }
            for file_record in product.digital_files.all()
        ],
    }


def _owned_product(user: CustomUser, pk: int) -> Product:
    product = (
        _seller_product_queryset()
        .filter(pk=pk, seller=user)
        .first()
    )
    if product is None:
        raise NotFound("Товар не знайдено.")
    return product


def _require_verified_seller(user: CustomUser) -> Wallet:
    if not SellerVerification.objects.filter(user=user, age__gte=18).exists():
        raise serializers.ValidationError(
            {"verification": "Спершу пройдіть одноразову верифікацію продавця."}
        )
    return Wallet.objects.get_or_create(user=user)[0]


def _require_editable(product: Product) -> None:
    """Заблокований чи видалений адміном товар продавець змінювати не може."""
    if product.moderation == "blocked":
        raise serializers.ValidationError(
            {"product": "Товар заблоковано адміністратором."}
        )
    if product.moderation == "deleted":
        raise serializers.ValidationError(
            {"product": "Товар видалено адміністратором."}
        )


def _save_variant(product: Product, payload: dict[str, Any]) -> ProductVariant:
    data = dict(payload)
    attribute_values = data.pop("attributes", [])
    data.pop("activation_codes", None)
    variant_id = data.pop("id", None)
    if variant_id:
        variant = ProductVariant.objects.filter(
            pk=variant_id, product=product
        ).first()
        if variant is None:
            raise serializers.ValidationError(
                {"variants": f"Варіант {variant_id} не належить цьому товару."}
            )
        if OrderItem.objects.filter(variant=variant).exists() and (
            "price_mc" in data and Decimal(data["price_mc"]) != variant.price_mc
        ):
            raise serializers.ValidationError(
                {"variants": "Ціну проданого варіанта змінити не можна."}
            )
        for key, value in data.items():
            setattr(variant, key, value)
        variant.save()
    else:
        variant = ProductVariant.objects.create(product=product, **data)

    for item in attribute_values:
        try:
            attribute_id = int(item["attribute_id"])
            value = str(item["value"]).strip()
        except (KeyError, TypeError, ValueError):
            raise serializers.ValidationError(
                {"variants": "Характеристика має містити attribute_id та value."}
            )
        if not value:
            continue
        category_ids = []
        category = product.category
        while category is not None:
            category_ids.append(category.id)
            category = category.parent
        if not Attribute.objects.filter(
            pk=attribute_id, category_id__in=category_ids
        ).exists():
            raise serializers.ValidationError(
                {"variants": f"Характеристика {attribute_id} не належить категорії товару."}
            )
        VariantAttributeValue.objects.filter(
            variant=variant, attribute_id=attribute_id
        ).exclude(value=value).delete()
        VariantAttributeValue.objects.get_or_create(
            variant=variant,
            attribute_id=attribute_id,
            value=value,
        )
    return variant


def _requires_digital_files(product: Product) -> bool:
    """Товар віддається файлом: digital_file або готова розробка."""
    return uses_digital_delivery(product.product_type, product.category.path)


def _paid_files_cover_variants(product: Product, files=None) -> bool:
    """Кожен варіант має платний файл (або є спільний платний файл без варіанта)."""
    records = list(files if files is not None else product.digital_files.all())
    paid = [record for record in records if not record.is_trial]
    if not paid:
        return False
    if any(record.variant_id is None for record in paid):
        return True
    variant_ids = set(product.variants.values_list("id", flat=True))
    return variant_ids.issubset({record.variant_id for record in paid})


def _variant_uses_activation_codes(variant: ProductVariant) -> bool:
    return uses_activation_codes(
        variant.product.product_type,
        [
            value.value
            for value in variant.attribute_values.all()
            if value.attribute.name == "Видача"
        ],
    )


def _activation_codes_cover_variants(product: Product) -> bool:
    variants = product.variants.select_related("product").prefetch_related(
        "attribute_values__attribute"
    )
    for variant in variants:
        if not _variant_uses_activation_codes(variant):
            continue
        available = ActivationCode.objects.filter(
            variant=variant,
            order_item__isnull=True,
        ).count()
        if available < variant.stock:
            return False
    return True


def _persist_product(product: Product, data: dict[str, Any]) -> Product:
    was_hidden = product.pk is not None and product.status == "hidden"
    category_id = data.pop("category_id")
    variants = data.pop("variants")
    product.category_id = category_id
    for field, value in data.items():
        setattr(product, field, value)
    product.save()
    for variant_data in variants:
        activation_codes = variant_data.get("activation_codes", [])
        variant = _save_variant(product, variant_data)
        if activation_codes:
            ActivationCode.objects.bulk_create(
                [
                    ActivationCode(
                        variant=variant,
                        code=code.strip(),
                        code_key=activation_code_key(code),
                    )
                    for code in activation_codes
                ]
            )

    if product.status in ("active", "hidden"):
        wallet = Wallet.objects.get(user=product.seller)
        if wallet.debt_mc > 0:
            raise serializers.ValidationError(
                {"wallet": "Поки є борг, активні оголошення недоступні."}
            )
        if _requires_digital_files(product) and not _paid_files_cover_variants(product):
            raise serializers.ValidationError(
                {"digital_files": "Додайте файл для покупця перед публікацією."}
            )
        if not _activation_codes_cover_variants(product):
            raise serializers.ValidationError(
                {"activation_codes": "Додайте код активації для кожної доступної одиниці."}
            )
    # Нуль залишку — автоматично «Закінчився»; поповнення повертає «У продажу»
    sync_stock_status(product)
    try:
        if product.status == "hidden" and not was_hidden:
            charge_hidden_listing_fee(product)
        recalculate_seller_deposit(product.seller, order=None)
    except DjangoValidationError as exc:
        message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
        raise serializers.ValidationError({"wallet": message}) from exc
    return product


class SellerProductsView(APIView):
    """GET/POST /api/products/mine."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadRateThrottle]

    def get(self, request):
        seller = cast(CustomUser, request.user)
        query = str(request.query_params.get("q") or "").strip()
        status_filter = str(request.query_params.get("status_filter") or "all")
        products = _seller_product_queryset().filter(seller=seller)
        if query:
            products = products.filter(name__icontains=query)
        if status_filter == "active":
            products = products.filter(status__in=["active", "hidden"])
        elif status_filter == "inactive":
            products = products.filter(status__in=["inactive", "sold_out"])
        product_rows = list(products)
        sold_variant_ids = set(
            OrderItem.objects.filter(
                variant__product_id__in=[product.pk for product in product_rows]
            ).values_list("variant_id", flat=True)
        )
        categories = list(Category.objects.filter(is_active=True).order_by("path"))
        parent_ids = {
            category.parent_id
            for category in categories
            if category.parent_id is not None
        }
        leaves = [
            {"id": category.id, "name": category.path}
            for category in categories
            if category.id not in parent_ids
        ]
        return Response(
            {
                "products": [
                    _seller_product_payload(product, sold_variant_ids)
                    for product in product_rows
                ],
                "categories": leaves,
            }
        )

    def post(self, request):
        seller = cast(CustomUser, request.user)
        _require_verified_seller(seller)
        payload = {key: request.data.get(key) for key in request.data.keys()}
        raw_variants = payload.get("variants")
        if isinstance(raw_variants, str):
            try:
                payload["variants"] = json.loads(raw_variants)
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError(
                    {"variants": "Не вдалося прочитати список варіантів."}
                ) from exc
        serializer = ProductInputSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        data = cast(dict[str, Any], serializer.validated_data)
        try:
            with transaction.atomic():
                product = Product(seller=seller)
                _persist_product(product, data)
        except IntegrityError as exc:
            if "activation" in str(exc).lower():
                raise serializers.ValidationError({
                    "variants": "Один або кілька кодів уже використані в іншому оголошенні."
                }) from exc
            raise
        product = _seller_product_queryset().get(pk=product.pk)
        return Response(_seller_product_payload(product), status=status.HTTP_201_CREATED)


class SellerActivationCodeCheckView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ActivationCodeCheckThrottle]

    def post(self, request):
        seller = cast(CustomUser, request.user)
        _require_verified_seller(seller)
        serializer = ActivationCodeCheckInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        codes = cast(list[str], serializer.validated_data["codes"])
        keys = [activation_code_key(code) for code in codes]
        existing_keys = set(
            ActivationCode.objects.filter(code_key__in=keys)
            .values_list("code_key", flat=True)
        )
        counts = {}
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
        available = []
        for key in keys:
            available.append(
                key not in existing_keys and counts[key] == 1
            )
        return Response({"available": available})


class SellerProductDetailView(APIView):
    """PATCH /api/products/mine/{id}; товари не видаляються фізично."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadRateThrottle]

    def get(self, request, pk):
        seller = cast(CustomUser, request.user)
        product = _owned_product(seller, pk)
        return Response(_seller_product_payload(product))

    def patch(self, request, pk):
        seller = cast(CustomUser, request.user)
        _require_verified_seller(seller)
        with transaction.atomic():
            product = (
                Product.objects.select_for_update()
                .filter(pk=pk, seller=seller)
                .first()
            )
            if product is None:
                raise NotFound("Товар не знайдено.")
            _require_editable(product)
            serializer = ProductInputSerializer(
                data=request.data,
                partial=True,
                context={"product": product},
            )
            serializer.is_valid(raise_exception=True)
            data = cast(dict[str, Any], serializer.validated_data)
            if "category_id" not in data:
                data["category_id"] = product.category_id
            if "variants" not in data:
                data["variants"] = []
            product = _persist_product(product, data)
        product = _seller_product_queryset().get(pk=product.pk)
        return Response(_seller_product_payload(product))


class SellerVariantRestockView(APIView):
    """POST /api/products/mine/{pk}/variants/{variant_id}/restock."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadRateThrottle]

    def post(self, request, pk, variant_id):
        seller = cast(CustomUser, request.user)
        serializer = VariantRestockInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = cast(dict[str, Any], serializer.validated_data)
        quantity = data["quantity"]
        codes = data.get("activation_codes", [])

        try:
            with transaction.atomic():
                _require_verified_seller(seller)
                product = (
                    Product.objects.select_for_update()
                    .select_related("category")
                    .filter(pk=pk, seller=seller)
                    .first()
                )
                if product is None:
                    raise NotFound("Товар не знайдено.")
                _require_editable(product)
                if product.status not in ("active", "hidden", "sold_out"):
                    raise serializers.ValidationError({
                        "product": "Спершу відновіть продаж товару, щоб поповнити залишок."
                    })
                if is_one_time_development_path(product.category.path):
                    raise serializers.ValidationError({
                        "quantity": "Готову розробку можна продати лише один раз."
                    })

                variant = ProductVariant.objects.select_for_update().filter(
                    pk=variant_id,
                    product=product,
                ).first()
                if variant is None:
                    raise NotFound("Варіант товару не знайдено.")
                if variant.stock + quantity > 2147483647:
                    raise serializers.ValidationError({
                        "quantity": "Залишок перевищує допустиму кількість."
                    })

                requires_codes = _variant_uses_activation_codes(variant)
                if requires_codes and len(codes) != quantity:
                    raise serializers.ValidationError({
                        "activation_codes": (
                            f"Додайте рівно {quantity} унікальних кодів — "
                            "по одному на кожну нову одиницю."
                        )
                    })
                if not requires_codes and codes:
                    raise serializers.ValidationError({
                        "activation_codes": "Для цього способу видачі коди не потрібні."
                    })

                code_keys = [activation_code_key(code) for code in codes]
                if len(set(code_keys)) != len(code_keys):
                    raise serializers.ValidationError({
                        "activation_codes": "Один і той самий код не можна додати кілька разів."
                    })
                if ActivationCode.objects.filter(code_key__in=code_keys).exists():
                    raise serializers.ValidationError({
                        "activation_codes": "Один або кілька кодів уже використані."
                    })
                if codes:
                    ActivationCode.objects.bulk_create([
                        ActivationCode(
                            variant=variant,
                            code=code,
                            code_key=code_key,
                        )
                        for code, code_key in zip(codes, code_keys)
                    ])

                variant.stock += quantity
                variant.save(update_fields=["stock"])
                product = sync_stock_status(product)
                if (
                    _requires_digital_files(product)
                    and not _paid_files_cover_variants(product)
                ):
                    raise serializers.ValidationError({
                        "digital_files": "Додайте платний файл для кожного варіанта перед поповненням."
                    })
                if not _activation_codes_cover_variants(product):
                    raise serializers.ValidationError({
                        "activation_codes": "Додайте код для кожної доступної одиниці."
                    })

                SellerDeposit.objects.select_for_update().get_or_create(
                    seller=seller,
                    defaults={"required_mc": Decimal("0")},
                )
                wallet = Wallet.objects.select_for_update().get(user=seller)
                locked_before = wallet.deposit_locked_mc
                try:
                    recalculate_seller_deposit(seller, allow_increase=True)
                except DjangoValidationError as exc:
                    message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                    raise serializers.ValidationError({"wallet": message}) from exc
                wallet.refresh_from_db(fields=["deposit_locked_mc"])
                additional_deposit = wallet.deposit_locked_mc - locked_before

                product = _seller_product_queryset().get(pk=product.pk)
                response_data = _seller_product_payload(product)
                response_data["additional_deposit_mc"] = str(additional_deposit)
        except IntegrityError as exc:
            if "activation" in str(exc).lower():
                raise serializers.ValidationError({
                    "activation_codes": "Один або кілька кодів уже використані."
                }) from exc
            raise

        return Response(response_data)


class SellerVariantPriceView(APIView):
    """POST /api/products/mine/{pk}/variants/{variant_id}/price."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadRateThrottle]

    def post(self, request, pk, variant_id):
        seller = cast(CustomUser, request.user)
        serializer = VariantPriceInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_price = cast(Decimal, serializer.validated_data["price_mc"])

        try:
            with transaction.atomic():
                _require_verified_seller(seller)
                product = (
                    Product.objects.select_for_update()
                    .filter(pk=pk, seller=seller)
                    .first()
                )
                if product is None:
                    raise NotFound("Товар не знайдено.")
                _require_editable(product)
                variant = ProductVariant.objects.select_for_update().filter(
                    pk=variant_id,
                    product=product,
                ).first()
                if variant is None:
                    raise NotFound("Варіант товару не знайдено.")
                if variant.price_mc != new_price and OrderItem.objects.filter(
                    variant=variant
                ).exists():
                    raise serializers.ValidationError({
                        "price_mc": "Ціну проданого варіанта змінити не можна."
                    })

                price_changed = variant.price_mc != new_price
                additional_deposit = Decimal("0")
                returned_deposit = Decimal("0")
                if price_changed:
                    variant.price_mc = new_price
                    variant.save(update_fields=["price_mc"])
                    SellerDeposit.objects.select_for_update().get_or_create(
                        seller=seller,
                        defaults={"required_mc": Decimal("0")},
                    )
                    wallet = Wallet.objects.select_for_update().get(user=seller)
                    locked_before = wallet.deposit_locked_mc
                    try:
                        recalculate_seller_deposit(seller, allow_increase=True)
                    except DjangoValidationError as exc:
                        message = (
                            exc.messages[0]
                            if getattr(exc, "messages", None)
                            else str(exc)
                        )
                        raise serializers.ValidationError({"wallet": message}) from exc
                    wallet.refresh_from_db(fields=["deposit_locked_mc"])
                    difference = wallet.deposit_locked_mc - locked_before
                    additional_deposit = max(difference, Decimal("0"))
                    returned_deposit = max(-difference, Decimal("0"))

                product = _seller_product_queryset().get(pk=product.pk)
                response_data = _seller_product_payload(product)
                response_data.update({
                    "price_changed": price_changed,
                    "additional_deposit_mc": str(additional_deposit),
                    "returned_deposit_mc": str(returned_deposit),
                    "return_delay_days": SiteSettings.get_solo().payout_freeze_days,
                })
        except DjangoValidationError as exc:
            message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            raise serializers.ValidationError({"wallet": message}) from exc

        return Response(response_data)


class SellerDigitalFilesView(APIView):
    """POST /api/products/mine/{id}/digital-files — приватний цифровий файл."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadRateThrottle]

    def post(self, request, pk):
        seller = cast(CustomUser, request.user)
        _require_verified_seller(seller)
        product = _owned_product(seller, pk)
        _require_editable(product)
        if not uses_digital_delivery(product.product_type, product.category.path):
            raise serializers.ValidationError(
                {"product": "Файли можна додавати лише до цифрового товару або готової розробки."}
            )
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            raise serializers.ValidationError({"file": "Додайте файл."})
        try:
            validate_upload(uploaded_file)
        except DjangoValidationError as exc:
            message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            raise serializers.ValidationError({"file": message}) from exc

        variant_id = request.data.get("variant_id")
        variant = None
        if variant_id:
            variant = ProductVariant.objects.filter(
                pk=variant_id, product=product
            ).first()
            if variant is None:
                raise serializers.ValidationError(
                    {"variant_id": "Варіант не належить цьому товару."}
                )
        file_record = DigitalFile.objects.create(
            product=product,
            variant=variant,
            file=uploaded_file,
            is_trial=str(request.data.get("is_trial", "")).strip().casefold()
            in ("1", "true", "yes", "on"),
            title=str(request.data.get("title") or "").strip(),
        )
        return Response(
            {
                "id": file_record.id,
                "title": file_record.title or uploaded_file.name,
                "variant_id": file_record.variant_id,
                "is_trial": file_record.is_trial,
            },
            status=status.HTTP_201_CREATED,
        )

    def delete(self, request, pk):
        seller = cast(CustomUser, request.user)
        _require_verified_seller(seller)
        product = _owned_product(seller, pk)
        _require_editable(product)
        file_id = request.query_params.get("file_id")
        file_record = DigitalFile.objects.filter(
            pk=file_id, product=product
        ).first()
        if file_record is None:
            raise NotFound("Файл не знайдено.")
        if DownloadGrant.objects.filter(file=file_record).exists():
            raise serializers.ValidationError(
                {"file": "Файл уже придбаний і не може бути видалений."}
            )
        if product.status in ("active", "hidden") and not file_record.is_trial:
            remaining = [
                record for record in product.digital_files.all()
                if record.id != file_record.id
            ]
            if not _paid_files_cover_variants(product, remaining):
                raise serializers.ValidationError(
                    {"file": "Спочатку зупиніть продаж."}
                )
        file_record.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


REVIEWS_PAGE_SIZE = 20


def _review_view_allowed(product: Product, user, token: str | None) -> bool:
    """Відгуки видно, якщо товар доступний напряму або за приватним токеном."""
    if _can_view(product, user):
        return True
    return bool(token) and token == product.hidden_token


def _reviewable_order(user, product: Product) -> Order | None:
    """Доставлене замовлення покупця з цим товаром, за яким ще немає відгуку."""
    if not user or not user.is_authenticated:
        return None
    orders = Order.objects.filter(
        buyer=user, status="delivered", items__variant__product=product
    )
    for order in orders:
        if not Review.objects.filter(
            author=user, order=order, product=product
        ).exists():
            return order
    return None


class ProductReviewView(APIView):
    """GET/POST /api/products/{id}/reviews — відгуки товару."""

    throttle_classes = [ReviewRateThrottle, UploadRateThrottle]

    def get_permissions(self):
        return [IsAuthenticated()] if self.request.method == "POST" else [AllowAny()]

    def get(self, request, pk):
        product = Product.objects.filter(pk=pk).first()
        if product is None:
            raise NotFound("Товар не знайдено.")
        token = request.query_params.get("token")
        if not _review_view_allowed(product, request.user, token):
            raise NotFound("Товар не знайдено.")
        try:
            offset = max(0, int(request.query_params.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        reviews = (
            Review.objects.filter(product=product)
            .select_related("author")
            .prefetch_related("media")
            .order_by("-created_at")[offset:offset + REVIEWS_PAGE_SIZE]
        )
        order = _reviewable_order(request.user, product)
        return Response(
            {
                "reviews": [
                    {
                        "id": review.id,
                        "author": review.author.username,
                        "text": review.text,
                        "created_at": review.created_at.strftime("%d.%m.%Y"),
                        "media": [
                            {"kind": media.kind, "url": media.file.url}
                            for media in review.media.all()
                        ],
                    }
                    for review in reviews
                ],
                "count": Review.objects.filter(product=product).count(),
                "can_review": order is not None,
                "order_id": order.id if order else None,
            }
        )

    def post(self, request, pk):
        reviewer = cast(CustomUser, request.user)
        product = Product.objects.filter(pk=pk).first()
        if product is None:
            raise NotFound("Товар не знайдено.")
        order_id = request.data.get("order_id")
        order = Order.objects.filter(
            pk=order_id,
            buyer=reviewer,
            status="delivered",
            items__variant__product=product,
        ).first()
        if order is None:
            raise serializers.ValidationError(
                {"order_id": "Відгук можна залишити лише після отримання цього товару."}
            )
        if Review.objects.filter(
            author=reviewer, order=order, product=product
        ).exists():
            raise serializers.ValidationError(
                {"order_id": "Ви можете залишити лише один коментар до цього товару."}
            )
        text = str(request.data.get("text") or "").strip()
        if not text:
            raise serializers.ValidationError({"text": "Напишіть текст відгуку."})

        uploads = request.FILES.getlist("media")
        prepared = []
        for uploaded_file in uploads:
            try:
                validate_upload(uploaded_file)
                extension = uploaded_file.name.rsplit(".", 1)[-1].lower()
                if extension in settings.ALLOWED_IMAGE_EXTENSIONS:
                    validate_image(uploaded_file)
                    kind = "photo"
                elif extension in settings.ALLOWED_VIDEO_EXTENSIONS:
                    kind = "video"
                else:
                    raise DjangoValidationError(
                        "До відгуку можна додавати лише фото або відео."
                    )
            except DjangoValidationError as exc:
                message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                raise serializers.ValidationError({"media": message}) from exc
            prepared.append((uploaded_file, kind))

        with transaction.atomic():
            review = Review.objects.create(
                product=product,
                author=reviewer,
                order=order,
                text=text,
            )
            for uploaded_file, kind in prepared:
                ReviewMedia.objects.create(
                    review=review,
                    file=uploaded_file,
                    kind=kind,
                )
        return Response(
            {"id": review.id, "product_id": product.id, "order_id": order.id, "text": review.text},
            status=status.HTTP_201_CREATED,
        )
