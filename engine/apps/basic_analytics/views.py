from datetime import date, timedelta

import logging

from django.core.cache import cache
from django.db.models import Count
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from config.permissions import DenyAPIKeyAccess, IsAdminUser
from engine.apps.basic_analytics.models import StoreDashboardStatsSnapshot
from engine.apps.customers.models import Customer
from engine.apps.orders.models import Order
from engine.apps.products.models import Product
from engine.apps.stores.models import Store
from engine.apps.support.models import SupportTicket
from engine.core.admin_dashboard_cache import (
    dashboard_live_overview_cache_key,
    dashboard_stats_cache_key,
)
from engine.core.request_context import get_dashboard_store_from_request
from engine.core.tenant_execution import tenant_scope_from_store

# Tenant-scoped cache for GET admin/basic-analytics/overview/ final JSON only.
DASHBOARD_STATS_CACHE_TTL_LIVE_SECONDS = 45
DASHBOARD_STATS_CACHE_TTL_HISTORICAL_SECONDS = 600
# Normalized day-bucket overview: short TTL (includes "today" in canonical window).
DASHBOARD_LIVE_OVERVIEW_TTL_SECONDS = 20

logger = logging.getLogger(__name__)


class BasicAnalyticsOverviewView(APIView):
    """
    Home dashboard stats: summary, time series, and meta (date range + bucket).

    Same JSON shape as the legacy admin stats overview endpoint.
    """

    permission_classes = [DenyAPIKeyAccess, IsAdminUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "standard_api"

    def _parse_date_range(self, request) -> tuple[date, date]:
        today = timezone.localdate()
        default_start = today - timedelta(days=29)

        start_str = request.query_params.get("start_date")
        end_str = request.query_params.get("end_date")

        start_date = default_start
        end_date = today

        try:
            if start_str:
                start_date = date.fromisoformat(start_str)
            if end_str:
                end_date = date.fromisoformat(end_str)
        except ValueError:
            start_date = default_start
            end_date = today

        if start_date > end_date:
            start_date, end_date = end_date, start_date

        return start_date, end_date

    def _get_bucket_func(self, bucket: str):
        bucket = (bucket or "day").lower()
        if bucket == "week":
            return TruncWeek
        if bucket == "month":
            return TruncMonth
        return TruncDate

    def _compute_payload(
        self,
        *,
        store: Store | None,
        start_date: date,
        end_date: date,
        bucket: str,
        bucket_func,
    ) -> dict:
        order_qs = Order.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        product_qs = Product.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        support_ticket_qs = SupportTicket.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        customer_qs = Customer.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        if store:
            order_qs = order_qs.filter(store=store)
            product_qs = product_qs.filter(store=store)
            support_ticket_qs = support_ticket_qs.filter(store=store)
            customer_qs = customer_qs.filter(store=store)

        summary = {
            "totalOrders": order_qs.count(),
            "totalProducts": product_qs.count(),
            "totalSupportTickets": support_ticket_qs.count(),
            "totalCustomers": customer_qs.count(),
        }

        series_map: dict[str, dict] = {}

        def _update_series(qs, key: str):
            for row in (
                qs.annotate(period=bucket_func("created_at"))
                .values("period")
                .annotate(total=Count("id"))
                .order_by("period")
            ):
                period = row["period"]
                if period is None:
                    continue
                label = getattr(period, "date", lambda: period)()
                label_str = label.isoformat()
                entry = series_map.setdefault(
                    label_str,
                    {
                        "label": label_str,
                        "orders": 0,
                        "products": 0,
                        "supportTickets": 0,
                        "customers": 0,
                    },
                )
                entry[key] = row["total"]

        _update_series(order_qs, "orders")
        _update_series(product_qs, "products")
        _update_series(support_ticket_qs, "supportTickets")
        _update_series(customer_qs, "customers")

        series = sorted(series_map.values(), key=lambda x: x["label"])

        # Ensure full date coverage for day bucket by filling gaps with zeros.
        if (bucket or "day").lower() == "day":
            filled_series = []
            current = start_date
            end_inclusive = end_date
            by_label = {entry["label"]: entry for entry in series}

            while current <= end_inclusive:
                label_str = current.isoformat()
                entry = by_label.get(label_str)
                if not entry:
                    entry = {
                        "label": label_str,
                        "orders": 0,
                        "products": 0,
                        "supportTickets": 0,
                        "customers": 0,
                    }
                filled_series.append(entry)
                current += timedelta(days=1)

            series = filled_series

        return {
            "summary": summary,
            "series": series,
            "meta": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "bucket": bucket,
            },
        }

    def get(self, request):
        start_date, end_date = self._parse_date_range(request)
        bucket = (request.query_params.get("bucket", "day") or "day").lower()
        bucket_func = self._get_bucket_func(bucket)
        explicit_range = bool(
            request.query_params.get("start_date")
            or request.query_params.get("end_date")
        )

        store = get_dashboard_store_from_request(request)
        if not store:
            logger.warning(
                "Tenant store context missing for basic analytics overview.",
                extra={
                    "path": getattr(request, "path", ""),
                    "user_public_id": getattr(getattr(request, "user", None), "public_id", None),
                },
            )
            return Response(
                {"detail": "Tenant (store) context is required"},
                status=400,
            )

        # Ensure strict tenant context is set before ANY queryset evaluation.
        with tenant_scope_from_store(store=store, reason="admin:basic_analytics_overview"):
            today = timezone.localdate()
            is_live_range = end_date >= today
            default_start = today - timedelta(days=29)
            default_end = today
            uses_default_range = (
                not explicit_range
                and start_date == default_start
                and end_date == default_end
            )

            # Default overview: one final cached payload per bucket (no post-cache slicing).
            if uses_default_range and bucket in ("day", "week", "month"):
                live_key = dashboard_live_overview_cache_key(store.public_id, bucket)
                cached = cache.get(live_key)
                if cached is not None:
                    return Response(cached)
                payload = self._compute_payload(
                    store=store,
                    start_date=start_date,
                    end_date=end_date,
                    bucket=bucket,
                    bucket_func=bucket_func,
                )
                cache.set(live_key, payload, DASHBOARD_LIVE_OVERVIEW_TTL_SECONDS)
                return Response(payload)

            cache_key = dashboard_stats_cache_key(
                store.public_id,
                start_date.isoformat(),
                end_date.isoformat(),
                bucket,
            )
            cached = cache.get(cache_key)
            if cached is not None:
                return Response(cached)

            cache_ttl = (
                DASHBOARD_STATS_CACHE_TTL_LIVE_SECONDS
                if is_live_range
                else DASHBOARD_STATS_CACHE_TTL_HISTORICAL_SECONDS
            )

            if bucket in ("week", "month") and not is_live_range:
                existing = StoreDashboardStatsSnapshot.objects.filter(
                    store=store,
                    start_date=start_date,
                    end_date=end_date,
                    bucket=bucket,
                ).first()
                if existing and existing.payload:
                    payload = existing.payload
                    cache.set(cache_key, payload, cache_ttl)
                    return Response(payload)

            payload = self._compute_payload(
                store=store,
                start_date=start_date,
                end_date=end_date,
                bucket=bucket,
                bucket_func=bucket_func,
            )

            should_snapshot = (
                not is_live_range
                and bucket in ("week", "month")
                and not explicit_range
            )
            if should_snapshot:
                StoreDashboardStatsSnapshot.objects.update_or_create(
                    store=store,
                    start_date=start_date,
                    end_date=end_date,
                    bucket=bucket,
                    defaults={"payload": payload},
                )

            cache.set(cache_key, payload, cache_ttl)
            return Response(payload)
