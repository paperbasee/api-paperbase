from django import forms
from django.contrib import admin
from django.utils.html import mark_safe

from engine.apps.stores.models import Store

from .admin_forms import ProductAdminForm, build_product_extra_form_fields
from .constants import MAX_PRODUCT_IMAGES_TOTAL
from .extra_schema import form_field_name_for_schema_item, get_product_extra_schema
from .models import (
    Category,
    Product,
    ProductImage,
    ProductAttribute,
    ProductAttributeValue,
    ProductVariant,
    ProductVariantAttribute,
)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    max_num = MAX_PRODUCT_IMAGES_TOTAL
    fields = ("image", "alt", "order")
    verbose_name_plural = "Gallery images"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
    list_display = [
        'name',
        'sku',
        'brand',
        'get_category',
        'price',
        'stock',
        'status',
        'is_active',
    ]
    list_editable = ['is_active']
    list_filter = ['category', 'status', 'is_active']
    search_fields = ['name', 'brand', 'sku']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline]
    autocomplete_fields = ['category', 'store']

    def _resolve_store(self, request, obj=None) -> Store | None:
        if obj and getattr(obj, "pk", None) and getattr(obj, "store_id", None):
            return obj.store
        if request.method == "POST" and request.POST.get("store"):
            try:
                return Store.objects.get(pk=request.POST["store"])
            except (Store.DoesNotExist, ValueError, TypeError):
                return None
        if request.method == "GET" and request.GET.get("store__id__exact"):
            # Useful when coming from a filtered changelist.
            try:
                return Store.objects.get(pk=request.GET["store__id__exact"])
            except (Store.DoesNotExist, ValueError, TypeError):
                return None
        return None

    def _extra_schema(self, request, obj=None) -> list[dict]:
        store = self._resolve_store(request, obj=obj)
        return get_product_extra_schema(store) if store else []

    def get_form(self, request, obj=None, change=False, **kwargs):
        """
        Provide a dynamic form class that includes extra_schema_* fields at class-definition time.

        Django admin validates `fieldsets` during `super().get_form(...)` and can raise FieldError
        before we get a chance to mutate `form.base_fields`. So we must pass a form class that
        already declares these fields.
        """
        schema = self._extra_schema(request, obj=obj)
        extra_fields = build_product_extra_form_fields(schema)

        base_form = kwargs.pop("form", None) or self.form
        if extra_fields:
            dynamic_form = type(
                "DynamicProductAdminForm",
                (base_form,),
                {**extra_fields},
            )
            kwargs["form"] = dynamic_form
        else:
            kwargs["form"] = base_form

        return super().get_form(request, obj=obj, change=change, **kwargs)

    def get_fieldsets(self, request, obj=None):
        base = [
            (
                None,
                {
                    "fields": (
                        "store",
                        "name",
                        "brand",
                        "slug",
                        "sku",
                        "status",
                        "category",
                    ),
                    "description": (
                        "Choose Store first when adding a product. Custom fields from that store’s "
                        "dashboard schema show after you save or if the form reloads with errors."
                    ),
                },
            ),
            ("Pricing", {"fields": ("price", "original_price")}),
            ("Media", {"fields": ("image",)}),
            ("Stock", {"fields": ("stock_tracking",)}),
            (
                "Additional Information",
                {"fields": ("description", "is_active")},
            ),
        ]

        schema = self._extra_schema(request, obj=obj)
        extra_names = [
            form_field_name_for_schema_item(str(it.get("id") or it.get("name") or ""))
            for it in schema
            if (it.get("name") or "").strip()
        ]

        if extra_names:
            base.append(
                (
                    "Custom fields (dashboard schema)",
                    {
                        "fields": tuple(extra_names),
                        "description": (
                            "Defined in Store settings → extra_field_schema (same as the merchant "
                            "dashboard). Values are saved on the product’s extra_data JSON field."
                        ),
                    },
                )
            )
        else:
            base.append(
                (
                    "Extra data (JSON)",
                    {
                        "fields": ("extra_data",),
                        "classes": ("collapse",),
                        "description": (
                            "No product custom fields are configured for the selected store yet. "
                            "Add them in the dashboard under Settings → Dynamic Fields, or edit JSON here."
                        ),
                    },
                )
            )

        return base

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'stock_tracking' and field:
            field.widget.attrs.update({'style': 'width: 5rem;'})
        return field

    def get_category(self, obj):
        return obj.category.name if obj.category else '-'

    get_category.short_description = 'Category'
    get_category.admin_order_field = 'category__name'


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'parent', 'order', 'is_active', 'product_count']
    list_filter = ['parent', 'is_active']
    list_editable = ['order', 'is_active']
    search_fields = ['name', 'slug']
    ordering = ['parent__name', 'order', 'name']
    readonly_fields = ['slug', 'image_preview']
    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'parent', 'description')
        }),
        ('Media', {
            'fields': ('image', 'image_preview'),
        }),
        ('Display', {
            'fields': ('order', 'is_active')
        }),
    )

    def image_preview(self, obj):
        if obj.image:
            return mark_safe(f'<img src="{obj.image.url}" style="max-height:120px;" />')
        return '(no image uploaded)'

    image_preview.short_description = 'Current Image'

    def product_count(self, obj):
        count = obj.products.count()
        return f"{count} products"

    product_count.short_description = 'Products'


class ProductAttributeValueInline(admin.TabularInline):
    model = ProductAttributeValue
    extra = 0
    ordering = ['order']


@admin.register(ProductAttribute)
class ProductAttributeAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'order']
    list_editable = ['order']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductAttributeValueInline]


@admin.register(ProductAttributeValue)
class ProductAttributeValueAdmin(admin.ModelAdmin):
    list_display = ['value', 'attribute', 'order']
    list_filter = ['attribute']
    list_editable = ['order']
    ordering = ['attribute', 'order']
    search_fields = ['value', 'attribute__name']


class ProductVariantAttributeInline(admin.TabularInline):
    model = ProductVariantAttribute
    extra = 0
    autocomplete_fields = ['attribute_value']


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ['product', 'sku', 'price_override', 'is_active', 'created_at']
    list_filter = ['is_active']
    list_editable = ['is_active']
    search_fields = ['sku', 'product__name']
    inlines = [ProductVariantAttributeInline]
    autocomplete_fields = ['product']
