from __future__ import annotations

import json

from django.core.cache import cache
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import RetrieveUpdateAPIView

from config.permissions import DenyAPIKeyAccess, IsStoreAdmin
from engine.core.request_context import get_dashboard_store_from_request
from engine.core.tenant_drf import ProvenTenantContextMixin

from .cache import PRESETS_CACHE_KEY, PRESETS_CACHE_TTL, get_cached_theme, invalidate_theme_cache, set_cached_theme
from .models import StorefrontTheme
from .permissions import IsThemeOwner, PresetsViewPermission, ThemeGetPermission
from .presets import PALETTE_LABELS, PALETTES, PALETTE_CHOICES
from .serializers import StorefrontThemeSerializer, serialize_theme_payload


class ThemeView(ProvenTenantContextMixin, RetrieveUpdateAPIView):
    serializer_class = StorefrontThemeSerializer
    allow_api_key = True

    def get_permissions(self):
        if self.request.method == "GET":
            return [ThemeGetPermission()]
        return [DenyAPIKeyAccess(), IsStoreAdmin(), IsThemeOwner()]

    def _resolve_store(self):
        if getattr(self.request, "api_key", None) and getattr(self.request, "store", None):
            return self.request.store
        return get_dashboard_store_from_request(self.request)

    def get_object(self) -> StorefrontTheme:
        store = self._resolve_store()
        if store is None:
            raise Http404()
        try:
            theme = StorefrontTheme.objects.get(store=store)
        except StorefrontTheme.DoesNotExist:
            raise Http404()
        return theme

    def get(self, request, *args, **kwargs):
        store = self._resolve_store()
        if store is None:
            raise Http404()
        cached = get_cached_theme(store.public_id)
        if cached is not None:
            return Response(cached)
        theme = self.get_object()
        data = serialize_theme_payload(theme)
        set_cached_theme(store.public_id, data)
        return Response(data)

    def patch(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def perform_update(self, serializer):
        theme = serializer.save()
        store = theme.store
        invalidate_theme_cache(store.public_id)
        # Warm cache with fresh payload
        data = serialize_theme_payload(theme)
        set_cached_theme(store.public_id, data)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        self.check_object_permissions(request, instance)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance.refresh_from_db()
        fresh = serialize_theme_payload(instance)
        return Response(fresh)


class PresetsView(APIView):
    permission_classes = [PresetsViewPermission]

    def get(self, request):
        try:
            raw = cache.get(PRESETS_CACHE_KEY)
        except Exception:
            raw = None
        if raw is not None:
            if isinstance(raw, dict):
                return Response(raw)
            try:
                return Response(json.loads(raw))
            except (TypeError, ValueError):
                pass

        presets_out = []
        for key in PALETTE_CHOICES:
            presets_out.append(
                {
                    "key": key,
                    "name": PALETTE_LABELS.get(key, key.title()),
                    "tokens": dict(PALETTES[key]),
                }
            )
        payload = {"presets": presets_out}
        try:
            cache.set(PRESETS_CACHE_KEY, json.dumps(payload), PRESETS_CACHE_TTL)
        except Exception:
            pass
        return Response(payload)
