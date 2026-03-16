from django.utils.text import slugify
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response

from config.permissions import IsStaffUser
from core.activity import log_activity
from core.models import ActivityLog
from .models import Brand, Category, NavbarCategory, Product, ProductImage
from .admin_serializers import (
    AdminBrandSerializer,
    AdminCategorySerializer,
    AdminNavbarCategorySerializer,
    AdminProductImageSerializer,
    AdminProductListSerializer,
    AdminProductSerializer,
)


class AdminProductViewSet(viewsets.ModelViewSet):
    permission_classes = [IsStaffUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    queryset = Product.objects.select_related(
        'category', 'sub_category',
    ).prefetch_related('images').all()
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.action == 'list':
            return AdminProductListSerializer
        return AdminProductSerializer

    def perform_create(self, serializer):
        instance = serializer.save()
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
        """Return { available: true } if no product has the given slug."""
        raw = request.query_params.get('slug', '').strip()
        if not raw:
            return Response({'available': True})
        normalized = slugify(raw)
        if not normalized:
            return Response({'available': True})
        exists = Product.objects.filter(slug=normalized).exists()
        return Response({'available': not exists})


class AdminProductImageViewSet(viewsets.ModelViewSet):
    permission_classes = [IsStaffUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminProductImageSerializer
    queryset = ProductImage.objects.all()


class AdminNavbarCategoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsStaffUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminNavbarCategorySerializer
    queryset = NavbarCategory.objects.prefetch_related('subcategories').all()

    def perform_create(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="navbar_category",
            entity_id=instance.pk,
            summary=f"Navbar category created: {getattr(instance, 'name', '')}".strip() or "Navbar category created",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="navbar_category",
            entity_id=instance.pk,
            summary=f"Navbar category updated: {getattr(instance, 'name', '')}".strip() or "Navbar category updated",
        )

    def perform_destroy(self, instance):
        name = getattr(instance, "name", "")
        pk = instance.pk
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="navbar_category",
            entity_id=pk,
            summary=f"Navbar category deleted: {name}" if name else "Navbar category deleted",
        )


class AdminCategoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsStaffUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminCategorySerializer
    queryset = Category.objects.select_related('navbar_category').all()

    def perform_create(self, serializer):
        instance = serializer.save()
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


class AdminBrandViewSet(viewsets.ModelViewSet):
    permission_classes = [IsStaffUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminBrandSerializer
    queryset = Brand.objects.all()

    def perform_create(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="brand",
            entity_id=instance.pk,
            summary=f"Brand created: {getattr(instance, 'name', '')}".strip() or "Brand created",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="brand",
            entity_id=instance.pk,
            summary=f"Brand updated: {getattr(instance, 'name', '')}".strip() or "Brand updated",
        )

    def perform_destroy(self, instance):
        name = getattr(instance, "name", "")
        pk = instance.pk
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="brand",
            entity_id=pk,
            summary=f"Brand deleted: {name}" if name else "Brand deleted",
        )
