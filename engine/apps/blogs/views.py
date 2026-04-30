from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsStorefrontAPIKey
from engine.apps.products.views import StorefrontTenantMixin
from engine.core.http_cache import storefront_cache_headers
from engine.core.permissions import IsModuleEnabled
from engine.core.tenancy import get_active_store, require_api_key_store

from . import services


def _module_gate() -> IsModuleEnabled:
    gate = IsModuleEnabled()
    gate.module_key = "blog"
    return gate


class _BlogStorefrontBase(StorefrontTenantMixin, APIView):
    """Shared storefront base: API key, tenant resolved, module gated."""

    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def get_permissions(self):
        return [IsStorefrontAPIKey(), _module_gate()]


class PublicBlogListView(_BlogStorefrontBase):
    """Published storefront blogs (optionally filtered by tag slug)."""

    def get(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        tag_slug = (request.query_params.get("tag") or "").strip() or None
        data = services.get_public_blogs(store, request, tag_slug=tag_slug)
        return Response(data)
    get = storefront_cache_headers(max_age=120)(get)


class PublicBlogDetailView(_BlogStorefrontBase):
    """Published storefront blog detail by public_id."""

    def get(self, request, public_id: str, *args, **kwargs):
        store = require_api_key_store(request)
        data = services.get_public_blog_detail(store, public_id, request)
        if data is None:
            return Response({"detail": "Not found."}, status=404)
        return Response(data)
    get = storefront_cache_headers(max_age=120)(get)
