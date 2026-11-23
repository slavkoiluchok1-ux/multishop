"""API каталогу: види товарів, дерево категорій, попап, товари."""
from collections import defaultdict
from typing import cast

from django.core.paginator import Paginator
from django.core.cache import cache
from django.db.models import Count, Exists, FloatField, Min, OuterRef, Q
from django.db.models.functions import Cast
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.favorites.models import Favorite

from .category_data import (
    ATTRIBUTES as CATEGORY_ATTRIBUTE_DEFINITIONS,
    ROOT_PRODUCT_TYPES,
    TREE as CATEGORY_TREE,
    VIEWS as CATEGORY_VIEWS,
    walk as walk_category_tree,
)
from .models import (
    Attribute,
    Category,
    DigitalFile,
    Product,
    ProductVariant,
    VariantAttributeValue,
)
from .pricing import dec, format_card, format_full

_CANONICAL_CATEGORY_PATHS = {
    tuple(part.casefold() for part in path)
    for path, _ in walk_category_tree(CATEGORY_TREE)
}
_CONFIGURED_CATEGORY_ATTRIBUTES = {
    (
        tuple(part.casefold() for part in path),
        definition["name"].strip().casefold(),
    )
    for definition in CATEGORY_ATTRIBUTE_DEFINITIONS
    for path in definition["targets"]
}

# Статуси, які бачить будь-хто (порожня категорія — де немає жодного з них)
VISIBLE_STATUSES = ("active", "sold_out")

# Три кнопки виду над каталогом: це фільтр за product_type, а не вузли дерева
ITEM_VIEWS = [
    {"key": key, "label": label, "types": CATEGORY_VIEWS[label]}
    for key, label in (
        ("physical", "Фізичні"),
        ("digital", "Цифрові"),
        ("services", "Послуги й підписки"),
    )
]


def types_for_view(view_key):
    """product_type для виду або None, якщо вид не задано."""
    for view in ITEM_VIEWS:
        if view["key"] == view_key:
            return view["types"]
    return None


def visible_products(types=None, availability=None):
    """Товари, які бачить покупець: активні із залишком і закінчені.

    Спільна логіка для списку, дерева категорій і фільтрів.
    availability: all | active | sold_out — звужує вибір за наявністю,
    не виходячи за межі VISIBLE_STATUSES (hidden та inactive не показуємо).
    Товари, заблоковані чи видалені адміном, не показуємо ніде.
    """
    in_stock = Exists(
        ProductVariant.objects.filter(product=OuterRef("pk"), stock__gt=0)
    )
    qs = Product.objects.filter(status__in=VISIBLE_STATUSES, moderation="ok")
    if availability == "active":
        # «В наявності»: активний і є хоча б один варіант із залишком
        qs = qs.filter(in_stock, status="active")
    elif availability == "sold_out":
        # «Закінчились»: статус sold_out або активний без залишку
        qs = qs.filter(Q(status="sold_out") | (Q(status="active") & ~in_stock))
    if types:
        qs = qs.filter(product_type__in=types)
    return qs


def _allowed_root_names(types):
    if not types:
        return {name for name in ROOT_PRODUCT_TYPES}
    return {
        name for name, allowed_types in ROOT_PRODUCT_TYPES.items()
        if set(allowed_types).intersection(types)
    }


def _root_category(cats, category):
    while category.parent_id:
        category = cats[category.parent_id]
    return category


def _category_map() -> dict[int, Category]:
    """{id: Category} — усе дерево за один запит."""
    return cast(
        dict[int, Category],
        cache.get_or_set(
            "catalog:category-map:v1",
            lambda: {c.id: c for c in Category.objects.all()},
            timeout=300,
        ),
    )


def _nonempty(cats, products):
    """Непорожні вузли й кількість товарів у піддереві кожного вузла."""
    parent_of = {
        cid: c.parent_id
        for cid, c in cats.items()
    }
    own = products.values_list("category_id").annotate(c=Count("id"))
    total = defaultdict(int)
    for leaf_id, count in own:
        node = leaf_id
        while node is not None:
            total[node] += count
            node = parent_of.get(node)
    return set(total), dict(total)


def _parents_with_children(cats, nonempty):
    """Вузли з активними нащадками, навіть коли вони ще без товарів."""
    parents = set()
    for cat in cats.values():
        if cat.parent_id and cat.is_active:
            parents.add(cat.parent_id)
    return parents


def _subtree_ids(cats, root_id):
    """id вузла й усіх його нащадків (без додаткових запитів до БД)."""
    children = defaultdict(list)
    for cat in cats.values():
        if cat.parent_id:
            children[cat.parent_id].append(cat.id)
    ids = {root_id}
    frontier = [root_id]
    while frontier:
        nxt = []
        for cid in frontier:
            for child in children.get(cid, []):
                if child not in ids:
                    ids.add(child)
                    nxt.append(child)
        frontier = nxt
    return ids


def _parse_categories(raw):
    """id категорій із параметра category (можна кілька через кому)."""
    result = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            result.append(int(chunk))
    return result


def _as_float(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _attr_groups(raw_values):
    """Групує attr=id:value за характеристикою. Порожнє значення означає «не вказано»."""
    groups = defaultdict(lambda: {"values": [], "unspecified": False})
    for raw in raw_values:
        aid_raw, sep, value = raw.partition(":")
        if not sep or (aid_raw != "brand" and not aid_raw.isdigit()):
            continue
        aid = aid_raw if aid_raw == "brand" else int(aid_raw)
        if value == "":
            groups[aid]["unspecified"] = True
        else:
            groups[aid]["values"].append(value)
    return groups


def _apply_attr_filters(qs, raw_values):
    """Звузити товари за обраними характеристиками (І між характеристиками)."""
    for aid, group in _attr_groups(raw_values).items():
        if aid == "brand":
            if group["values"]:
                qs = qs.filter(brand__in=group["values"])
            elif group["unspecified"]:
                qs = qs.filter(Q(brand__isnull=True) | Q(brand=""))
            continue
        if group["values"]:
            ranges = [v for v in group["values"] if ".." in v]
            if ranges:
                # «мін..макс» для числових характеристик
                lo, _, hi = ranges[0].partition("..")
                sub = VariantAttributeValue.objects.filter(
                    variant__product=OuterRef("pk"), attribute_id=aid
                ).annotate(num=Cast("value", FloatField()))
                lo_val, hi_val = _as_float(lo), _as_float(hi)
                if lo_val is not None:
                    sub = sub.filter(num__gte=lo_val)
                if hi_val is not None:
                    sub = sub.filter(num__lte=hi_val)
            else:
                sub = VariantAttributeValue.objects.filter(
                    variant__product=OuterRef("pk"), attribute_id=aid, value__in=group["values"]
                )
            qs = qs.filter(Exists(sub))
        elif group["unspecified"]:
            has_any = VariantAttributeValue.objects.filter(
                variant__product=OuterRef("pk"), attribute_id=aid
            )
            qs = qs.filter(~Exists(has_any))
    return qs


def _attr_payload(attr):
    """Характеристика для панелі фільтра."""
    options = [
        {"id": o.id, "value": o.value}
        for o in attr.options.all()
    ]
    if attr.name == "Тип смартфона":
        type_order = {
            "Сенсорний": 0,
            "Кнопковий": 1,
            "Захищений": 2,
            "Складний": 3,
            "Інший": 4,
        }
        options.sort(key=lambda option: type_order.get(option["value"], len(type_order)))
    return {
        "id": attr.id,
        "name": attr.name,
        "kind": attr.kind,
        "unit": attr.unit,
        "required": attr.required,
        "options": options,
    }


def _sort_phone_attributes(attributes):
    """Показує тип і конструкцію смартфона одразу після бренду."""
    priority = {"Тип смартфона": 0, "Конструкція": 1}
    return sorted(
        attributes,
        key=lambda attribute: (
            priority.get(attribute.name, 2),
            attribute.order,
            attribute.name,
        ),
    )


def _effective_attributes(cats, category_ids):
    """Повертає успадковані характеристики, де найближча категорія має пріоритет."""
    by_category = defaultdict(list)
    for attribute in (
        Attribute.objects.filter(category_id__in=_category_ancestor_ids(cats, category_ids))
        .exclude(name="Бренд")
        .order_by("order", "name")
        .select_related("category")
        .prefetch_related("options")
    ):
        if not _is_configured_category_attribute(attribute):
            continue
        by_category[attribute.category_id].append(attribute)

    effective = {}
    for category_id in category_ids:
        category = cats.get(category_id)
        seen_names = set()
        while category is not None:
            for attribute in by_category.get(category.id, []):
                if attribute.name not in seen_names:
                    effective[attribute.id] = attribute
                    seen_names.add(attribute.name)
            category = (
                cats.get(category.parent_id)
                if category.parent_id
                else None
            )
    return sorted(effective.values(), key=lambda attribute: (attribute.order, attribute.name))


def _category_ancestor_ids(cats, category_ids):
    ancestors = set()
    for category_id in category_ids:
        category = cats.get(category_id)
        while category is not None:
            ancestors.add(category.id)
            category = (
                cats.get(category.parent_id)
                if category.parent_id
                else None
            )
    return ancestors


def _is_configured_category_attribute(attribute):
    """Не показує застарілі DB-атрибути для категорій із канонічного дерева."""
    path = tuple(part.strip().casefold() for part in attribute.category.path.split(" › "))
    if path not in _CANONICAL_CATEGORY_PATHS:
        return True
    return (
        path,
        attribute.name.strip().casefold(),
    ) in _CONFIGURED_CATEGORY_ATTRIBUTES


def _brand_payload(products):
    """Virtual filter backed by Product.brand."""
    values = set(products.exclude(brand="").values_list("brand", flat=True).distinct())
    values.update(("Apple", "Google", "Samsung", "Xiaomi"))
    return {
        "id": "brand",
        "name": "Бренд",
        "kind": "single",
        "unit": "",
        "required": False,
        "options": [{"id": value, "value": value} for value in sorted(values, key=str.casefold)],
    }


def _category_item(cat, total, has_kids):
    return {
        "id": cat.id,
        "name": cat.name,
        "path": cat.path,
        "product_types": list(ROOT_PRODUCT_TYPES.get(cat.name, ()))
        if cat.parent_id is None
        else [],
        "product_count": total.get(cat.id, 0),
        "has_children": cat.id in has_kids,
    }


def _favorite_ids(user, product_ids):
    """id товарів з обраного в межах переданого списку (один запит)."""
    if not user or not user.is_authenticated or not product_ids:
        return set()
    return set(
        Favorite.objects.filter(user=user, product_id__in=product_ids).values_list(
            "product_id", flat=True
        )
    )


def _card(product, favorite_ids):
    """Картка товару для каталогу."""
    variants = list(product.variants.all())
    price = min((v.price_mc for v in variants), default=None)
    category_parts = [part for part in (product.category.path or "").split("›") if part.strip()]
    category_root = category_parts[0].strip() if category_parts else ""
    category_leaf = category_parts[-1].strip() if category_parts else product.category.name
    image = None
    if product.main_image:
        image = product.main_image.url
    else:
        for variant in variants:
            if variant.image:
                image = variant.image.url
                break
    return {
        "id": product.id,
        "name": product.name,
        "slug": product.slug,
        "product_type": product.product_type,
        "product_type_display": product.get_product_type_display(),
        "status": product.status,
        "price_mc": dec(price) or "0",
        "price_card": format_card(price),
        "image": image,
        "category_path": product.category.path,
        "category_root": category_root,
        "category_leaf": category_leaf,
        "brand": product.brand,
        "seller": product.seller.username,
        "is_favorite": product.id in favorite_ids,
        # Наявність рахуємо з уже завантажених варіантів, без нових запитів.
        "in_stock": any(v.stock > 0 for v in variants),
    }


def _is_staff_admin(user) -> bool:
    """True для адміністратора будь-якого рівня."""
    return bool(user and user.is_authenticated and getattr(user, "is_staff_admin", False))


def _can_view(product, user):
    """Чи доступний товар цьому користувачу напряму (не за токеном)."""
    # Заблокований або видалений товар бачить лише адміністратор
    if product.is_moderated:
        return _is_staff_admin(user)
    if product.status in VISIBLE_STATUSES:
        return True
    if user and user.is_authenticated:
        if product.seller_id == user.id:
            return True
        return _is_staff_admin(user)
    return False


def _breadcrumbs(category):
    """Ланцюг від кореня до вузла."""
    chain = []
    node = category
    while node is not None:
        chain.append(node)
        node = node.parent
    chain.reverse()
    return chain


def _detail(product, request):
    """Повні дані товару для сторінки товару."""
    variants = [
        {
            "id": v.id,
            "sku": v.sku,
            "price_mc": str(v.price_mc),
            "price_full": format_full(v.price_mc),
            "price_card": format_card(v.price_mc),
            "stock": v.stock,
            "license_type": v.license_type,
            "license_type_display": v.get_license_type_display(),
            "image": v.image.url if v.image else None,
            "attribute_values": [
                {"attribute": av.attribute.name, "value": av.value, "unit": av.attribute.unit}
                for av in v.attribute_values.all()
            ],
        }
        for v in product.variants.all()
    ]

    chain = _breadcrumbs(product.category)
    attributes = Attribute.objects.filter(
        category_id__in=[c.id for c in chain]
    ).order_by("order", "name").prefetch_related("options")

    images = [img.image.url for img in product.images.all() if img.image]
    if product.main_image:
        images.insert(0, product.main_image.url)

    favorite_ids = _favorite_ids(request.user, [product.id])
    return {
        "id": product.id,
        "name": product.name,
        "slug": product.slug,
        "description": product.description,
        "brand": product.brand,
        "portfolio_url": product.portfolio_url,
        "product_type": product.product_type,
        "product_type_display": product.get_product_type_display(),
        "status": product.status,
        "moderation": product.moderation,
        "moderation_display": product.get_moderation_display(),  # pyright: ignore[reportAttributeAccessIssue]
        "moderation_reason": product.moderation_reason,
        "category": {"id": product.category_id, "name": product.category.name, "path": product.category.path},
        "breadcrumbs": [{"id": c.id, "name": c.name} for c in chain],
        "seller": {
            "id": product.seller_id,
            "username": product.seller.username,
            "avatar": product.seller.avatar.url if product.seller.avatar else None,
        },
        "images": images,
        "main_image": product.main_image.url if product.main_image else None,
        "attributes": [
            {
                "id": a.id,  # pyright: ignore[reportAttributeAccessIssue]
                "name": a.name,
                "kind": a.kind,
                "unit": a.unit,
                "required": a.required,
                "options": [{"id": o.id, "value": o.value} for o in a.options.all()],  # pyright: ignore[reportAttributeAccessIssue]
            }
            for a in attributes
        ],
        "variants": variants,
        "trial_files": [
            {
                "id": file_record.id,
                "title": file_record.title or (file_record.file.name or "").rsplit("/", 1)[-1],
                "url": f"/api/products/{product.id}/trial-files/{file_record.id}",
            }
            for file_record in DigitalFile.objects.filter(
                product=product, is_trial=True
            )
        ],
        "is_favorite": product.id in favorite_ids,
    }


class CatalogViewsView(APIView):
    """GET /api/catalog/views — три кнопки виду над каталогом."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"views": ITEM_VIEWS})


class CatalogCategoriesView(APIView):
    """GET /api/catalog/categories?view=&parent= — вузли дерева без порожніх."""

    permission_classes = [AllowAny]

    def get(self, request):
        view_key = request.query_params.get("view") or ""
        types = types_for_view(view_key)
        availability = request.query_params.get("availability") or "all"
        cats = _category_map()
        nonempty, total = _nonempty(cats, visible_products(types, availability))
        has_kids = _parents_with_children(cats, nonempty)

        parent_raw = request.query_params.get("parent")
        parent_id = int(parent_raw) if parent_raw and parent_raw.isdigit() else None

        nodes = sorted(
            (
                c for c in cats.values()
                if c.is_active
                and c.parent_id == parent_id
                and (
                    parent_id is not None
                    or c.name in _allowed_root_names(types)
                )
            ),
            key=lambda c: (c.order, c.name),
        )
        return Response(
            {
                "view": view_key,
                "parent": parent_id,
                "categories": [_category_item(c, total, has_kids) for c in nodes],
            }
        )


class CatalogCategoryPopupView(APIView):
    """GET /api/catalog/categories/{id}/popup — характеристики й підтипи вузла."""

    permission_classes = [AllowAny]

    def get(self, request, pk):
        view_key = request.query_params.get("view") or ""
        types = types_for_view(view_key)
        availability = request.query_params.get("availability") or "all"
        category = get_object_or_404(Category, pk=pk)

        cats = _category_map()
        if _root_category(cats, category).name not in _allowed_root_names(types):
            raise Http404("Розділ недоступний для цього виду товарів")
        nonempty, total = _nonempty(cats, visible_products(types, availability))
        has_kids = _parents_with_children(cats, nonempty)
        chain = _breadcrumbs(category)

        children = sorted(
            (
                c for c in cats.values()
                if c.parent_id == category.id and c.is_active
            ),
            key=lambda c: (c.order, c.name),
        )
        attributes = (
            _effective_attributes(cats, [category.id])
            if not children
            else []
        )
        is_phone_leaf = (
            not children
            and category.path.startswith("Електроніка › Смартфони й зв'язок › Смартфони")
        )
        if is_phone_leaf:
            attributes = _sort_phone_attributes(attributes)
            scoped_products = visible_products(types, availability).filter(
                category_id__in=_subtree_ids(cats, category.id)
            )
            attributes_payload = [_brand_payload(scoped_products)]
        else:
            attributes_payload = []
        attributes_payload.extend(_attr_payload(attribute) for attribute in attributes)
        return Response(
            {
                "category": {"id": category.id, "name": category.name, "path": category.path},
                "breadcrumbs": [{"id": c.id, "name": c.name} for c in chain],
                "attributes": attributes_payload,
                "children": [_category_item(c, total, has_kids) for c in children],
            }
        )


class CatalogFiltersView(APIView):
    """GET /api/catalog/filters?view=&category=1,2 — характеристики для фільтра."""

    permission_classes = [AllowAny]

    def get(self, request):
        params = request.query_params
        category_ids = _parse_categories(params.get("category"))
        if not category_ids:
            return Response({"attributes": []})

        cats = _category_map()
        leaf_ids = []
        for cid in category_ids:
            category = cats.get(cid)
            if category is None:
                continue
            has_active_children = any(
                child.is_active
                and child.parent_id == category.id
                for child in cats.values()
            )
            if not has_active_children:
                leaf_ids.append(cid)  # pyright: ignore[reportAttributeAccessIssue]

        if not leaf_ids:
            return Response({"attributes": []})

        types = types_for_view(params.get("view") or "")
        availability = params.get("availability") or "all"
        attributes = []
        for leaf_id in leaf_ids:
            leaf = cats[leaf_id]
            if leaf.path.startswith("Електроніка › Смартфони й зв'язок › Смартфони"):
                attributes.append(
                    _brand_payload(
                        visible_products(types, availability).filter(
                            category_id__in=_subtree_ids(cats, leaf_id)
                        )
                    )
                )
                attributes.extend(
                    _attr_payload(attribute)
                    for attribute in _sort_phone_attributes(
                        _effective_attributes(cats, [leaf_id])
                    )
                )
            else:
                attributes.extend(
                    _attr_payload(attribute)
                    for attribute in _effective_attributes(cats, [leaf_id])
                )
        return Response({"attributes": attributes})


class ProductListView(APIView):
    """GET /api/products — каталог із пошуком, фільтрами, сортуванням, пагінацією."""

    permission_classes = [AllowAny]

    def get(self, request):
        params = request.query_params
        types = types_for_view(params.get("view") or "")
        qs = visible_products(types, params.get("availability") or "all")

        # Вузли дерева: можна вибрати кілька, кожен включає всіх нащадків
        category_ids = _parse_categories(params.get("category"))
        if category_ids:
            cats = _category_map()
            node_ids = set()
            for cid in category_ids:
                if cid in cats:
                    node_ids.update(_subtree_ids(cats, cid))
            qs = qs.filter(category_id__in=node_ids)

        seller = params.get("seller")
        if seller:
            qs = qs.filter(seller__username=seller)

        query = (params.get("q") or "").strip()
        if query:
            qs = qs.filter(
                Q(name__icontains=query)
                | Q(brand__icontains=query)
                | Q(description__icontains=query)
            )

        # Фільтри за характеристиками: attr=id:value (порожнє значення = «не вказано»)
        qs = _apply_attr_filters(qs, params.getlist("attr"))

        # select_related + prefetch: список товарів без N+1
        qs = qs.select_related("category", "seller").prefetch_related("variants")
        qs = qs.annotate(min_price=Min("variants__price_mc"))

        sort = params.get("sort") or "time"
        if sort == "price_asc":
            qs = qs.order_by("min_price", "-created_at")
        elif sort == "price_desc":
            qs = qs.order_by("-min_price", "-created_at")
        else:
            qs = qs.order_by("-created_at")

        paginator = Paginator(qs, 24)
        page = paginator.get_page(params.get("page"))
        favorite_ids = _favorite_ids(request.user, [p.id for p in page.object_list])
        return Response(
            {
                "count": paginator.count,
                "page": page.number,
                "pages": paginator.num_pages,
                "results": [_card(p, favorite_ids) for p in page.object_list],
            }
        )


class ProductDetailView(APIView):
    """GET /api/products/{id} — сторінка товару."""

    permission_classes = [AllowAny]

    def get(self, request, pk):
        product = get_object_or_404(
            Product.objects.select_related("category", "seller").prefetch_related(
                "variants__attribute_values__attribute", "images"
            ),
            pk=pk,
        )
        if not _can_view(product, request.user):
            raise Http404("Товар недоступний")
        return Response(_detail(product, request))


class ProductHiddenView(APIView):
    """GET /api/products/h/{token} — прихований товар за посиланням."""

    permission_classes = [AllowAny]

    def get(self, request, token):
        product = get_object_or_404(
            Product.objects.select_related("category", "seller").prefetch_related(
                "variants__attribute_values__attribute", "images"
            ),
            hidden_token=token,
        )
        if product.status != "hidden":
            raise Http404("Товар недоступний")
        # Заблокований чи видалений товар не відкривається навіть за посиланням
        if product.is_moderated and not _is_staff_admin(request.user):
            raise Http404("Товар недоступний")
        return Response(_detail(product, request))
