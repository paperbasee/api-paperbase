from __future__ import annotations

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsStorefrontAPIKey
from engine.core import cache_service
from engine.core.http_cache import storefront_cache_headers
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
        cache_key = f"cache:{store.public_id}:store_public:v1"
        cached = cache_service.get(cache_key)
        if cached is not None:
            return Response(cached)
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

        # Pixel ID is a public-facing identifier — safe to expose in this
        # storefront-scoped response. Allows tracker.js to self-configure
        # without requiring the storefront to set global JS variables.
        pixel_id = None
        try:
            from engine.apps.marketing_integrations.models import MarketingIntegration

            integration = (
                MarketingIntegration.objects.filter(
                    store=store,
                    provider=MarketingIntegration.Provider.FACEBOOK,
                    is_active=True,
                )
                .values("pixel_id")
                .first()
            )
            if integration:
                pixel_id = integration["pixel_id"] or None
        except Exception:
            pixel_id = None

        payload = {
            "store_name": store.name,
            "logo_url": absolute_media_url(store.logo, request),
            "language": (getattr(settings_row, "language", "") or "en") if settings_row else "en",
            "currency": store.currency,
            "currency_symbol": store.currency_symbol or "",
            "country": public_extra.get("country") or "",
            "support_email": store.contact_email or "",
            "phone": store.phone or "",
            "address": store.address or "",
            "extra_field_schema": extra_schema,
            "modules_enabled": modules,
            # Tracking script versioning (global, deploy-scoped; not per tenant)
            "tracker_build_id": getattr(settings, "TRACKER_BUILD_ID", ""),
            "tracker_script_src": (
                "https://storage.paperbase.me/static/tracker.js"
                f"?v={getattr(settings, 'TRACKER_BUILD_ID', '')}"
            ),
            "tracking_ingest_endpoint": "https://api.paperbase.me/tracking/event",
            # Public pixel ID for tracker.js self-configuration.
            # None when no active Facebook integration exists.
            "pixel_id": pixel_id,
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
        cache_service.set(cache_key, payload, settings.CACHE_TTL_STORE_SETTINGS)
        return Response(payload)
    get = storefront_cache_headers(max_age=60)(get)
