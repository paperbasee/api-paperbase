from decimal import Decimal, InvalidOperation

from django.db.models import Count, Sum
from django.db.models import Q
from django.utils.text import slugify
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response

from config.permissions import IsPlatformSuperuser
from engine.core.activity import log_activity
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store
from .models import (
    Category,
    Product,
    ProductAttribute,
    ProductAttributeValue,
    ProductImage,
    ProductVariant,
)
from .admin_serializers import (
    AdminCategorySerializer,
    AdminParentCategorySerializer,
    AdminProductAttributeSerializer,
    AdminProductAttributeValueSerializer,
    AdminProductImageSerializer,
    AdminProductListSerializer,
    AdminProductSerializer,
    AdminProductVariantSerializer,
)
from .services import invalidate_category_cache, invalidate_product_cache
from .stock_sync import sync_product_stock_from_variants


class AdminProductViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    queryset = (
        Product.objects.select_related("category")
        .prefetch_related("images")
        .annotate(
            _admin_variant_count=Count("variants", distinct=False),
            _admin_variant_stock_sum=Sum("variants__stock_quantity"),
        )
        .all()
    )
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        qs = qs.filter(store=ctx.store).order_by("-created_at", "id")

        status_value = (self.request.query_params.get("status") or "").strip().lower()
        if status_value == "active":
            qs = qs.filter(is_active=True)
        elif status_value == "inactive":
            qs = qs.filter(is_active=False)

        stock_filter = (self.request.query_params.get("stock") or "").strip().lower()
        if stock_filter == "in_stock":
            qs = qs.filter(
                Q(_admin_variant_count=0, stock__gt=0)
                | Q(_admin_variant_count__gt=0, _admin_variant_stock_sum__gt=0)
            )
        elif stock_filter == "out_of_stock":
            qs = qs.filter(
                Q(_admin_variant_count=0, stock=0)
                | Q(_admin_variant_count__gt=0, _admin_variant_stock_sum__lte=0)
            )
        elif stock_filter == "low_stock":
            qs = qs.filter(
                Q(_admin_variant_count=0, stock__gt=0, stock__lte=5)
                | Q(
                    _admin_variant_count__gt=0,
                    _admin_variant_stock_sum__gt=0,
                    _admin_variant_stock_sum__lte=5,
                )
            )

        category_public_id = (self.request.query_params.get("category") or "").strip()
        if category_public_id:
            qs = qs.filter(category__public_id=category_public_id)

        try:
            if "price_min" in self.request.query_params:
                price_min = Decimal((self.request.query_params.get("price_min") or "").strip())
                qs = qs.filter(price__gte=price_min)
            if "price_max" in self.request.query_params:
                price_max = Decimal((self.request.query_params.get("price_max") or "").strip())
                qs = qs.filter(price__lte=price_max)
        except (InvalidOperation, ValueError):
            pass

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(sku__icontains=search)
                | Q(brand__icontains=search)
            )

        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return AdminProductListSerializer
        return AdminProductSerializer

    def get_permissions(self):
        if self.action == "destroy":
            return [IsPlatformSuperuser()]
        return super().get_permissions()

    def get_serializer_context(self):
        ctx = get_active_store(self.request)
        return {
            **super().get_serializer_context(),
            "store_id": ctx.store.pk if ctx.store else None,
        }

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValidationError(
                {
                    "detail": (
                        "No active store resolved. Re-login, switch store, or send the "
                        "X-Store-ID header."
                    )
                }
            )
        instance = serializer.save(store=store)
        invalidate_product_cache(store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="product",
            entity_id=instance.public_id,
            summary=f"Product created: {instance.name}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        ctx = get_active_store(self.request)
        if ctx.store:
            invalidate_product_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="product",
            entity_id=instance.public_id,
            summary=f"Product updated: {instance.name}",
        )

    def perform_destroy(self, instance):
        name = getattr(instance, "name", "")
        public_id = instance.public_id
        ctx = get_active_store(self.request)
        super().perform_destroy(instance)
        if ctx.store:
            invalidate_product_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="product",
            entity_id=public_id,
            summary=f"Product deleted: {name}" if name else "Product deleted",
        )

    @action(detail=False, methods=['get'], url_path='check-slug')
    def check_slug(self, request):
        """Return { available: true } if no product has the given slug in this store."""
        raw = request.query_params.get('slug', '').strip()
        if not raw:
            return Response({'available': True})
        normalized = slugify(raw)
        if not normalized:
            return Response({'available': True})
        ctx = get_active_store(request)
        if not ctx.store:
            return Response({'available': True})
        qs = Product.objects.filter(store=ctx.store)
        exclude_public_id = (request.query_params.get('exclude_public_id') or '').strip()
        if exclude_public_id:
            qs = qs.exclude(public_id=exclude_public_id)
        exists = qs.filter(slug=normalized).exists()
        return Response({'available': not exists})


class AdminProductImageViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminProductImageSerializer
    queryset = ProductImage.objects.select_related('product').all()
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        return qs.filter(product__store=ctx.store)

    def get_serializer_context(self):
        ctx = get_active_store(self.request)
        return {
            **super().get_serializer_context(),
            "store_id": ctx.store.pk if ctx.store else None,
        }

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValidationError(
                {
                    'detail': (
                        'No active store resolved. Re-login, switch store, or send the '
                        'X-Store-ID header.'
                    )
                }
            )
        product = serializer.validated_data['product']
        if product.store_id != store.id:
            raise ValidationError(
                {'product': 'This product does not belong to your active store.'}
            )
        serializer.save()
        invalidate_product_cache(store.public_id)


class AdminParentCategoryViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    """Top-level (parent) categories in nested hierarchy. Served at /admin/parent-categories/."""
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminParentCategorySerializer
    queryset = Category.objects.filter(parent__isnull=True).order_by('order', 'name')
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        return qs.filter(store=ctx.store)

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValidationError(
                {
                    "detail": (
                        "No active store resolved. Re-login, switch store, or send the "
                        "X-Store-ID header."
                    )
                }
            )
        instance = serializer.save(parent=None, store=store)
        invalidate_category_cache(store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="category",
            entity_id=instance.public_id,
            summary=f"Parent category created: {getattr(instance, 'name', '')}".strip() or "Parent category created",
        )

    def perform_update(self, serializer):
        instance = serializer.save(parent=None)
        ctx = get_active_store(self.request)
        if ctx.store:
            invalidate_category_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="category",
            entity_id=instance.public_id,
            summary=f"Parent category updated: {getattr(instance, 'name', '')}".strip() or "Parent category updated",
        )

    def perform_destroy(self, instance):
        name = getattr(instance, "name", "")
        public_id = instance.public_id
        ctx = get_active_store(self.request)
        super().perform_destroy(instance)
        if ctx.store:
            invalidate_category_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="category",
            entity_id=public_id,
            summary=f"Parent category deleted: {name}" if name else "Parent category deleted",
        )


class AdminCategoryViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    """Subcategories (parent is not null). Served at /admin/categories/."""
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminCategorySerializer
    queryset = Category.objects.filter(parent__isnull=False).select_related('parent').order_by('parent', 'order', 'name')
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        return qs.filter(store=ctx.store)

    def get_serializer_context(self):
        ctx = get_active_store(self.request)
        return {
            **super().get_serializer_context(),
            "store_id": ctx.store.pk if ctx.store else None,
        }

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValidationError(
                {
                    "detail": (
                        "No active store resolved. Re-login, switch store, or send the "
                        "X-Store-ID header."
                    )
                }
            )
        instance = serializer.save(store=store)
        invalidate_category_cache(store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="category",
            entity_id=instance.public_id,
            summary=f"Category created: {getattr(instance, 'name', '')}".strip() or "Category created",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        ctx = get_active_store(self.request)
        if ctx.store:
            invalidate_category_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="category",
            entity_id=instance.public_id,
            summary=f"Category updated: {getattr(instance, 'name', '')}".strip() or "Category updated",
        )

    def perform_destroy(self, instance):
        name = getattr(instance, "name", "")
        public_id = instance.public_id
        ctx = get_active_store(self.request)
        super().perform_destroy(instance)
        if ctx.store:
            invalidate_category_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="category",
            entity_id=public_id,
            summary=f"Category deleted: {name}" if name else "Category deleted",
        )


class AdminProductVariantViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    serializer_class = AdminProductVariantSerializer
    queryset = (
        ProductVariant.objects.select_related("product")
        .prefetch_related("attribute_values__attribute_value__attribute")
        .all()
    )
    lookup_field = "public_id"

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        qs = qs.filter(product__store=ctx.store)
        product_id = self.request.query_params.get("product")
        if product_id:
            qs = qs.filter(product__public_id=product_id)
        return qs.order_by("product__public_id", "sku", "id")

    def get_serializer_context(self):
        ctx = get_active_store(self.request)
        return {
            **super().get_serializer_context(),
            "store_id": ctx.store.pk if ctx.store else None,
        }

    def _ensure_product_in_store(self, product: Product) -> None:
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValidationError(
                {
                    "detail": (
                        "No active store resolved. Re-login, switch store, or send the "
                        "X-Store-ID header."
                    )
                }
            )
        if product.store_id != store.id:
            raise ValidationError(
                {"product": "This product does not belong to your active store."}
            )

    def perform_create(self, serializer):
        product = serializer.validated_data["product"]
        self._ensure_product_in_store(product)
        instance = serializer.save()
        sync_product_stock_from_variants(instance.product_id)
        ctx = get_active_store(self.request)
        if ctx.store:
            invalidate_product_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="product_variant",
            entity_id=instance.public_id,
            summary=f"Variant created: {instance.sku} ({instance.product.name})",
        )

    def perform_update(self, serializer):
        product = serializer.validated_data.get("product", serializer.instance.product)
        self._ensure_product_in_store(product)
        instance = serializer.save()
        sync_product_stock_from_variants(instance.product_id)
        ctx = get_active_store(self.request)
        if ctx.store:
            invalidate_product_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="product_variant",
            entity_id=instance.public_id,
            summary=f"Variant updated: {instance.sku}",
        )

    def perform_destroy(self, instance):
        self._ensure_product_in_store(instance.product)
        pid = instance.product_id
        sku = instance.sku
        variant_public_id = instance.public_id
        ctx = get_active_store(self.request)
        super().perform_destroy(instance)
        sync_product_stock_from_variants(pid)
        if ctx.store:
            invalidate_product_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="product_variant",
            entity_id=variant_public_id,
            summary=f"Variant deleted: {sku}",
        )


class AdminProductAttributeViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    """Global attribute definitions (Color, Size, …) shared across stores."""
    serializer_class = AdminProductAttributeSerializer
    queryset = ProductAttribute.objects.prefetch_related("values").order_by("order", "name")
    lookup_field = "public_id"

    def perform_create(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="product_attribute",
            entity_id=instance.public_id,
            summary=f"Product attribute created: {instance.name}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="product_attribute",
            entity_id=instance.public_id,
            summary=f"Product attribute updated: {instance.name}",
        )

    def perform_destroy(self, instance):
        public_id = instance.public_id
        name = instance.name
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="product_attribute",
            entity_id=public_id,
            summary=f"Product attribute deleted: {name}",
        )


class AdminProductAttributeValueViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    serializer_class = AdminProductAttributeValueSerializer
    queryset = ProductAttributeValue.objects.select_related("attribute").order_by(
        "attribute", "order", "value"
    )
    lookup_field = "public_id"

    def get_queryset(self):
        qs = super().get_queryset()
        # Do NOT accept ?attribute=<int> (internal PK) — use attribute_public_id instead
        attr_public_id = self.request.query_params.get("attribute_public_id")
        if attr_public_id:
            qs = qs.filter(attribute__public_id=attr_public_id)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="product_attribute_value",
            entity_id=instance.public_id,
            summary=f"Attribute value created: {instance}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="product_attribute_value",
            entity_id=instance.public_id,
            summary=f"Attribute value updated: {instance}",
        )

    def perform_destroy(self, instance):
        public_id = instance.public_id
        label = str(instance)
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="product_attribute_value",
            entity_id=public_id,
            summary=f"Attribute value deleted: {label}",
        )

