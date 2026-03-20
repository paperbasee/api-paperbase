"""
Analytics integration service.

Delegates all tracking calls to the marketing_integrations dispatcher
which resolves active integrations per store and fans out to provider-
specific services (Facebook, etc.).

The module-level ``meta_conversions`` singleton preserves the import
interface used across the codebase.
"""

from engine.apps.marketing_integrations.services import dispatcher


class AnalyticsService:
    """Thin proxy that forwards every call to the marketing dispatcher."""

    def track_view_content(self, request, product):
        dispatcher.track_view_content(request, product)

    def track_search(self, request, query: str):
        dispatcher.track_search(request, query)

    def track_add_to_cart(self, request, product, quantity: int) -> None:
        dispatcher.track_add_to_cart(request, product, quantity)

    def track_add_to_wishlist(self, request, product) -> None:
        dispatcher.track_add_to_wishlist(request, product)

    def track_initiate_checkout(self, request) -> None:
        dispatcher.track_initiate_checkout(request)

    def track_add_payment_info(self, request, order_data: dict | None = None) -> None:
        dispatcher.track_add_payment_info(request, order_data)

    def track_purchase(self, request, order) -> None:
        dispatcher.track_purchase(request, order)

    def track_contact(self, request) -> None:
        dispatcher.track_contact(request)


# Module-level singleton — kept as meta_conversions for backward compatibility.
meta_conversions = AnalyticsService()
