from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsStorefrontAPIKey
from engine.core.media_urls import absolute_media_url
from engine.core.tenancy import require_api_key_store, require_resolved_store

from .social_links import normalize_social_links_from_storefront_public


def _product_only_extra_field_schema(raw):
    if not isinstance(raw, list):
        return []
    out = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        entity = row.get("entityType") or row.get("entity_type") or "product"
        if str(entity).lower() == "product":
            out.append(row)
    return out


class StorePublicView(APIView):
    """Read-only storefront branding and public configuration (API key)."""

    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_resolved_store(request)

    def get(self, request):
        store = require_api_key_store(request)
        settings_row = getattr(store, "settings", None)
        if settings_row is None:
            from engine.apps.stores.models import StoreSettings

            settings_row = StoreSettings.objects.filter(store=store).first()
        public_extra = (
            (settings_row.storefront_public or {}) if settings_row else {}
        )
        theme = public_extra.get("theme_settings") or {}
        if not isinstance(theme, dict):
            theme = {}
        seo = public_extra.get("seo") or {}
        if not isinstance(seo, dict):
            seo = {}
        policy_urls = public_extra.get("policy_urls") or {}
        if not isinstance(policy_urls, dict):
            policy_urls = {}

        social_links = normalize_social_links_from_storefront_public(public_extra)

        extra_schema = []
        modules: dict = {}
        if settings_row:
            raw_schema = getattr(settings_row, "extra_field_schema", None)
            extra_schema = _product_only_extra_field_schema(raw_schema)
            raw_modules = getattr(settings_row, "modules_enabled", None)
            if isinstance(raw_modules, dict):
                modules = {k: bool(v) for k, v in raw_modules.items()}

        payload = {
            "store_name": store.name,
            "logo_url": absolute_media_url(store.logo, request),
            "currency": store.currency,
            "currency_symbol": store.currency_symbol or "",
            "country": public_extra.get("country") or "",
            "support_email": store.contact_email or "",
            "phone": store.phone or "",
            "address": store.address or "",
            "extra_field_schema": extra_schema,
            "modules_enabled": modules,
            "theme_settings": {
                "primary_color": theme.get("primary_color") or "",
            },
            "seo": {
                "default_title": seo.get("default_title") or "",
                "default_description": seo.get("default_description") or "",
            },
            "policy_urls": {
                "returns": policy_urls.get("returns") or "",
                "refund": policy_urls.get("refund") or "",
                "privacy": policy_urls.get("privacy") or "",
            },
            "social_links": social_links,
        }
        return Response(payload)
