"""
Marketing / Meta Conversions API tracking facade.

Delegates to the marketing_integrations dispatcher. Import as ``meta_conversions``
from storefront views (orders, products, search).
"""

from engine.apps.marketing_integrations.services import dispatcher


class AnalyticsService:
    """Thin proxy that forwards every call to the marketing dispatcher."""

    def track_product_detail_view(self, request, product):
        dispatcher.track_view_content(request, product)

    def track_checkout_started(self, request) -> None:
        dispatcher.track_initiate_checkout(request)

    def track_purchase(self, request, order) -> None:
        dispatcher.track_purchase(request, order)

    def track_view_content(self, request, product) -> None:
        dispatcher.track_view_content(request, product)

    def track_initiate_checkout(self, request) -> None:
        dispatcher.track_initiate_checkout(request)

    def track_search(self, request, query: str):
        dispatcher.track_search(request, query)


# Module-level singleton — import as meta_conversions across the codebase.
meta_conversions = AnalyticsService()
