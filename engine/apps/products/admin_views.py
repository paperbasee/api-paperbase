from django.db.models import Count, Sum
from django.utils.text import slugify
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
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
from .stock_sync import sync_product_stock_from_variants


class AdminProductViewSet(viewsets.ModelViewSet):
    permission_classes = [IsDashboardUser]
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
    lookup_field = 'pk'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if ctx.store:
            return qs.filter(store=ctx.store)
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return AdminProductListSerializer
        return AdminProductSerializer

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
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="product",
            entity_id=instance.pk,
            summary=f"Product created: {instance.name}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="product",
            entity_id=instance.pk,
            summary=f"Product updated: {instance.name}",
        )

    def perform_destroy(self, instance):
        name = getattr(instance, "name", "")
        pk = instance.pk
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="product",
            entity_id=pk,
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
        qs = Product.objects.all()
        ctx = get_active_store(request)
        if ctx.store:
            qs = qs.filter(store=ctx.store)
        exists = qs.filter(slug=normalized).exists()
        return Response({'available': not exists})


class AdminProductImageViewSet(viewsets.ModelViewSet):
    permission_classes = [IsDashboardUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminProductImageSerializer
    queryset = ProductImage.objects.select_related('product').all()

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if ctx.store:
            return qs.filter(product__store=ctx.store)
        return qs

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


class AdminParentCategoryViewSet(viewsets.ModelViewSet):
    """Top-level (parent) categories in nested hierarchy. Served at /admin/parent-categories/."""
    permission_classes = [IsDashboardUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminParentCategorySerializer
    queryset = Category.objects.filter(parent__isnull=True).order_by('order', 'name')

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if ctx.store:
            return qs.filter(store=ctx.store)
        return qs

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
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="category",
            entity_id=instance.pk,
            summary=f"Parent category created: {getattr(instance, 'name', '')}".strip() or "Parent category created",
        )

    def perform_update(self, serializer):
        instance = serializer.save(parent=None)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="category",
            entity_id=instance.pk,
            summary=f"Parent category updated: {getattr(instance, 'name', '')}".strip() or "Parent category updated",
        )

    def perform_destroy(self, instance):
        name = getattr(instance, "name", "")
        pk = instance.pk
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="category",
            entity_id=pk,
            summary=f"Parent category deleted: {name}" if name else "Parent category deleted",
        )


class AdminCategoryViewSet(viewsets.ModelViewSet):
    """Subcategories (parent is not null). Served at /admin/categories/."""
    permission_classes = [IsDashboardUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminCategorySerializer
    queryset = Category.objects.filter(parent__isnull=False).select_related('parent').order_by('parent', 'order', 'name')

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if ctx.store:
            return qs.filter(store=ctx.store)
        return qs

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
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="category",
            entity_id=instance.pk,
            summary=f"Category created: {getattr(instance, 'name', '')}".strip() or "Category created",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="category",
            entity_id=instance.pk,
            summary=f"Category updated: {getattr(instance, 'name', '')}".strip() or "Category updated",
        )

    def perform_destroy(self, instance):
        name = getattr(instance, "name", "")
        pk = instance.pk
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="category",
            entity_id=pk,
            summary=f"Category deleted: {name}" if name else "Category deleted",
        )


class AdminProductVariantViewSet(viewsets.ModelViewSet):
    permission_classes = [IsDashboardUser]
    serializer_class = AdminProductVariantSerializer
    queryset = (
        ProductVariant.objects.select_related("product")
        .prefetch_related("attribute_values__attribute_value__attribute")
        .all()
    )
    lookup_field = "pk"

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if ctx.store:
            qs = qs.filter(product__store=ctx.store)
        product_id = self.request.query_params.get("product")
        if product_id:
            qs = qs.filter(product_id=product_id)
        return qs.order_by("product_id", "sku", "id")

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
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="product_variant",
            entity_id=instance.pk,
            summary=f"Variant created: {instance.sku} ({instance.product.name})",
        )

    def perform_update(self, serializer):
        product = serializer.validated_data.get("product", serializer.instance.product)
        self._ensure_product_in_store(product)
        instance = serializer.save()
        sync_product_stock_from_variants(instance.product_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="product_variant",
            entity_id=instance.pk,
            summary=f"Variant updated: {instance.sku}",
        )

    def perform_destroy(self, instance):
        self._ensure_product_in_store(instance.product)
        pid = instance.product_id
        sku = instance.sku
        variant_pk = instance.pk
        super().perform_destroy(instance)
        sync_product_stock_from_variants(pid)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="product_variant",
            entity_id=variant_pk,
            summary=f"Variant deleted: {sku}",
        )


class AdminProductAttributeViewSet(viewsets.ModelViewSet):
    """Global attribute definitions (Color, Size, …) shared across stores."""

    permission_classes = [IsDashboardUser]
    serializer_class = AdminProductAttributeSerializer
    queryset = ProductAttribute.objects.prefetch_related("values").order_by("order", "name")

    def perform_create(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="product_attribute",
            entity_id=instance.pk,
            summary=f"Product attribute created: {instance.name}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="product_attribute",
            entity_id=instance.pk,
            summary=f"Product attribute updated: {instance.name}",
        )

    def perform_destroy(self, instance):
        pk = instance.pk
        name = instance.name
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="product_attribute",
            entity_id=pk,
            summary=f"Product attribute deleted: {name}",
        )


class AdminProductAttributeValueViewSet(viewsets.ModelViewSet):
    permission_classes = [IsDashboardUser]
    serializer_class = AdminProductAttributeValueSerializer
    queryset = ProductAttributeValue.objects.select_related("attribute").order_by(
        "attribute", "order", "value"
    )

    def get_queryset(self):
        qs = super().get_queryset()
        attr_id = self.request.query_params.get("attribute")
        if attr_id:
            qs = qs.filter(attribute_id=attr_id)
        return qs

    def perform_create(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="product_attribute_value",
            entity_id=instance.pk,
            summary=f"Attribute value created: {instance}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="product_attribute_value",
            entity_id=instance.pk,
            summary=f"Attribute value updated: {instance}",
        )

    def perform_destroy(self, instance):
        pk = instance.pk
        label = str(instance)
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="product_attribute_value",
            entity_id=pk,
            summary=f"Attribute value deleted: {label}",
        )

