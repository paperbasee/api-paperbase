from datetime import timedelta

from django.utils import timezone
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser

from engine.core.activity import log_activity
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.media_deletion_service import schedule_media_deletion
from engine.core.models import ActivityLog
from engine.core.permissions import IsModuleEnabled
from engine.core.tenancy import get_active_store

from . import services
from .admin_serializers import (
    AdminBlogSerializer,
    AdminBlogTagSerializer,
)
from engine.utils.bd_query import filter_by_bd_date
from engine.utils.time import bd_today

from .models import Blog, BlogTag


def _apply_module_gate(base_permissions):
    """Prepend IsModuleEnabled (module_key='blog') to a list of DRF permission instances."""
    gate = IsModuleEnabled()
    gate.module_key = "blog"
    return [gate, *base_permissions]


class _BlogModuleGateMixin:
    """Inject the blog module toggle into the permission pipeline."""

    module_key = "blog"

    def get_permissions(self):
        return _apply_module_gate(super().get_permissions())


class AdminBlogViewSet(_BlogModuleGateMixin, StoreRolePermissionMixin, viewsets.ModelViewSet):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminBlogSerializer
    queryset = Blog.objects.all()
    lookup_field = "public_id"

    def get_queryset(self):
        qs = super().get_queryset().select_related("author").prefetch_related("tags")
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        qs = qs.filter(store=ctx.store, is_deleted=False)

        params = self.request.query_params

        tag = (params.get("tag") or "").strip()
        if tag:
            qs = qs.filter(tags__public_id=tag).distinct()

        q = (params.get("q") or params.get("search") or "").strip()
        if q:
            qs = qs.filter(title__icontains=q)

        published_date = (params.get("published_date") or "").strip().lower()
        if published_date == "today":
            qs = filter_by_bd_date(qs, "published_at", bd_today())
        elif published_date == "last_7_days":
            qs = qs.filter(published_at__gte=timezone.now() - timedelta(days=7))
        elif published_date == "last_30_days":
            qs = qs.filter(published_at__gte=timezone.now() - timedelta(days=30))

        return qs

    def get_serializer_context(self):
        ctx = get_active_store(self.request)
        return {
            **super().get_serializer_context(),
            "store_id": ctx.store.pk if ctx.store else None,
        }

    def _require_store(self):
        ctx = get_active_store(self.request)
        if not ctx.store:
            raise ValidationError(
                {
                    "detail": (
                        "No active store resolved. Re-login, switch store, or send the "
                        "X-Store-ID header."
                    )
                }
            )
        return ctx.store

    def perform_create(self, serializer):
        store = self._require_store()
        user = self.request.user if self.request.user.is_authenticated else None
        instance = serializer.save(store=store, author=user)
        services.ensure_blog_published(instance)
        services.invalidate_blog_cache(store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="blog",
            entity_id=instance.public_id,
            summary=f"Blog created: {instance.title or instance.public_id}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        services.ensure_blog_published(instance)
        ctx = get_active_store(self.request)
        if ctx.store:
            services.invalidate_blog_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="blog",
            entity_id=instance.public_id,
            summary=f"Blog updated: {instance.title or instance.public_id}",
        )

    def perform_destroy(self, instance):
        title = instance.title or instance.public_id
        public_id = instance.public_id
        schedule_media_deletion(instance)
        services.soft_delete_blog(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="blog",
            entity_id=public_id,
            summary=f"Blog deleted: {title}",
        )


class AdminBlogTagViewSet(
    _BlogModuleGateMixin, StoreRolePermissionMixin, viewsets.ModelViewSet
):
    serializer_class = AdminBlogTagSerializer
    queryset = BlogTag.objects.all()
    lookup_field = "public_id"

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        return qs.filter(store=ctx.store)

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        if not ctx.store:
            raise ValidationError({"detail": "No active store resolved."})
        instance = serializer.save(store=ctx.store)
        services.invalidate_blog_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="blog_tag",
            entity_id=instance.public_id,
            summary=f"Blog tag created: {instance.name}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        ctx = get_active_store(self.request)
        if ctx.store:
            services.invalidate_blog_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="blog_tag",
            entity_id=instance.public_id,
            summary=f"Blog tag updated: {instance.name}",
        )

    def perform_destroy(self, instance):
        name = instance.name
        public_id = instance.public_id
        ctx = get_active_store(self.request)
        super().perform_destroy(instance)
        if ctx.store:
            services.invalidate_blog_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="blog_tag",
            entity_id=public_id,
            summary=f"Blog tag deleted: {name}",
        )
