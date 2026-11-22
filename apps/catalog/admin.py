"""Адмінка каталогу: категорії-дерево, товари, варіанти, файли, відгуки."""
from django.contrib import admin

from .models import (
    Attribute,
    AttributeOption,
    Category,
    DigitalFile,
    Product,
    ProductImage,
    ProductVariant,
    Review,
    ReviewMedia,
    VariantAttributeValue,
)


class AttributeInline(admin.TabularInline):
    model = Attribute
    extra = 0


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("path", "name", "parent", "is_active", "order")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "path")
    list_editable = ("order",)
    prepopulated_fields = {"slug": ("name",)}
    inlines = [AttributeInline]


class AttributeOptionInline(admin.TabularInline):
    model = AttributeOption
    extra = 0


@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "kind", "unit", "required", "order")
    list_filter = ("kind", "required", "category")
    search_fields = ("name",)
    inlines = [AttributeOptionInline]


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0


class DigitalFileInline(admin.TabularInline):
    model = DigitalFile
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "seller", "category", "product_type", "status", "updated_at")
    list_filter = ("product_type", "status", "category", "seller")
    search_fields = ("name", "brand", "description", "slug")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("hidden_token", "created_at", "updated_at")
    inlines = [ProductVariantInline, ProductImageInline, DigitalFileInline]


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("product", "sku", "price_mc", "stock", "license_type")
    list_filter = ("license_type",)
    search_fields = ("product__name", "sku")


@admin.register(VariantAttributeValue)
class VariantAttributeValueAdmin(admin.ModelAdmin):
    list_display = ("variant", "attribute", "value")
    search_fields = ("variant__product__name", "attribute__name", "value")


class ReviewMediaInline(admin.TabularInline):
    model = ReviewMedia
    extra = 0


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "author", "order", "created_at")
    search_fields = ("product__name", "author__username", "text")
    inlines = [ReviewMediaInline]
