from decimal import Decimal
from datetime import date, timedelta

from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from rest_framework.exceptions import PermissionDenied

from config.permissions import IsDashboardUser, IsStoreAdmin
from engine.core.tenancy import get_active_store
from engine.apps.stores.models import Store
from engine.apps.orders.models import Order
from engine.apps.orders.admin_serializers import AdminOrderListSerializer
from engine.apps.billing.feature_gate import require_feature
from engine.apps.products.models import Product, Category
from engine.apps.support.models import SupportTicket
from engine.apps.notifications.models import StorefrontCTA
from engine.apps.cart.models import Cart, CartItem
from engine.apps.wishlist.models import WishlistItem
from engine.apps.analytics.models import StoreDashboardStatsSnapshot


class DashboardStatsView(APIView):
    permission_classes = [IsDashboardUser]

    def get(self, request):
        ctx = get_active_store(request)
        store = ctx.store

        order_qs = Order.objects.all()
        product_qs = Product.objects.all()
        category_qs = Category.objects.all()
        support_ticket_qs = SupportTicket.objects.all()
        notification_qs = StorefrontCTA.objects.filter(is_active=True)
        cart_qs = Cart.objects.filter(items__isnull=False)
        wishlist_qs = WishlistItem.objects.all()

        if store:
            order_qs = order_qs.filter(store=store)
            product_qs = product_qs.filter(store=store)
            category_qs = category_qs.filter(store=store)
            support_ticket_qs = support_ticket_qs.filter(store=store)
            notification_qs = notification_qs.filter(store=store)
            cart_qs = cart_qs.filter(items__product__store=store)
            wishlist_qs = wishlist_qs.filter(product__store=store)
        else:
            notification_qs = notification_qs.none()

        order_counts = order_qs.aggregate(
            total_count=Count('id'),
            pending_count=Count('id', filter=Q(status=Order.Status.PENDING)),
            confirmed_count=Count('id', filter=Q(status=Order.Status.CONFIRMED)),
            cancelled_count=Count('id', filter=Q(status=Order.Status.CANCELLED)),
        )
        revenue_agg = order_qs.exclude(
            status=Order.Status.CANCELLED,
        ).aggregate(revenue=Sum('total'))

        product_stats = product_qs.aggregate(
            total_count=Count('id'),
            active_count=Count('id', filter=Q(is_active=True)),
            oos_count=Count('id', filter=Q(stock=0, is_active=True)),
        )

        recent_orders = order_qs.prefetch_related('items__product')[:10]

        return Response({
            'orders': {
                'total': order_counts['total_count'],
                'pending': order_counts['pending_count'],
                'confirmed': order_counts['confirmed_count'],
                'cancelled': order_counts['cancelled_count'],
            },
            'revenue': str(revenue_agg['revenue'] or Decimal('0.00')),
            'products': {
                'total': product_stats['total_count'],
                'active': product_stats['active_count'],
                'out_of_stock': product_stats['oos_count'],
            },
            'categories': category_qs.filter(parent__isnull=True).count(),
            'subcategories': category_qs.filter(parent__isnull=False).count(),
            'support_tickets': support_ticket_qs.count(),
            'notifications': notification_qs.count(),
            # Count only carts that actually have at least one item
            'carts': cart_qs.distinct().count(),
            'wishlist': wishlist_qs.count(),
            'recent_orders': AdminOrderListSerializer(recent_orders, many=True).data,
        })


class DashboardStatsOverviewView(APIView):
    """
    Non-premium "home stats" endpoint.

    Returns the same payload shape as the premium
    `/api/v1/admin/analytics/overview/` endpoint:
      - summary
      - series
      - meta { start_date, end_date, bucket }
    """

    permission_classes = [IsDashboardUser]

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
        cart_item_qs = CartItem.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        wishlist_qs = WishlistItem.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        support_ticket_qs = SupportTicket.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        if store:
            order_qs = order_qs.filter(store=store)
            product_qs = product_qs.filter(store=store)
            cart_item_qs = cart_item_qs.filter(product__store=store)
            wishlist_qs = wishlist_qs.filter(product__store=store)
            support_ticket_qs = support_ticket_qs.filter(store=store)

        summary = {
            "totalOrders": order_qs.count(),
            "totalProducts": product_qs.count(),
            "totalCartItems": cart_item_qs.count(),
            "totalWishlistItems": wishlist_qs.count(),
            "totalSupportTickets": support_ticket_qs.count(),
        }

        series_map: dict[str, dict] = {}

        def _update_series(qs, key: str):
            for row in qs.annotate(period=bucket_func("created_at")).values("period").annotate(total=Count("id")).order_by("period"):
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
                        "cartItems": 0,
                        "wishlistItems": 0,
                        "supportTickets": 0,
                    },
                )
                entry[key] = row["total"]

        _update_series(order_qs, "orders")
        _update_series(product_qs, "products")
        _update_series(cart_item_qs, "cartItems")
        _update_series(wishlist_qs, "wishlistItems")
        _update_series(support_ticket_qs, "supportTickets")

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
                        "cartItems": 0,
                        "wishlistItems": 0,
                        "supportTickets": 0,
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

        ctx = get_active_store(request)
        store = ctx.store
        if not store:
            return Response({"summary": {}, "series": [], "meta": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "bucket": bucket}})

        existing = StoreDashboardStatsSnapshot.objects.filter(
            store=store,
            start_date=start_date,
            end_date=end_date,
            bucket=bucket,
        ).first()
        # Ranges that include "today" are live and should not be served from a
        # stale snapshot indefinitely because underlying records can change.
        is_live_range = end_date >= timezone.localdate()
        if existing and existing.payload and not is_live_range:
            return Response(existing.payload)

        payload = self._compute_payload(
            store=store,
            start_date=start_date,
            end_date=end_date,
            bucket=bucket,
            bucket_func=bucket_func,
        )

        StoreDashboardStatsSnapshot.objects.update_or_create(
            store=store,
            start_date=start_date,
            end_date=end_date,
            bucket=bucket,
            defaults={"payload": payload},
        )
        return Response(payload)


class DashboardAnalyticsView(APIView):
    """
    Date-filtered summary and time-series stats for the admin dashboard.

    This endpoint is separate from DashboardStatsView to avoid changing the
    existing global counters used in the UI navigation.
    """

    permission_classes = [IsDashboardUser]

    def _parse_date_range(self, request) -> tuple[date, date]:
        """Parse start/end date from query params, defaulting to the last 30 days."""
        today = timezone.localdate()
        default_start = today - timedelta(days=29)

        start_str = request.query_params.get('start_date')
        end_str = request.query_params.get('end_date')

        start_date = default_start
        end_date = today

        try:
            if start_str:
                start_date = date.fromisoformat(start_str)
            if end_str:
                end_date = date.fromisoformat(end_str)
        except ValueError:
            # Fallback silently to defaults on parsing errors.
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

    def get(self, request):
        start_date, end_date = self._parse_date_range(request)
        bucket = request.query_params.get('bucket', 'day')
        bucket_func = self._get_bucket_func(bucket)
        ctx = get_active_store(request)
        store = ctx.store

        # Feature gate: analytics overview is only available on plans that enable it.
        # This is intentionally enforced at backend level (not just UI gating).
        require_feature(request.user, "advanced_analytics")

        # Filter base querysets by date range (inclusive) and active store.
        order_qs = Order.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        product_qs = Product.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        cart_item_qs = CartItem.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        wishlist_qs = WishlistItem.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        support_ticket_qs = SupportTicket.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        if store:
            order_qs = order_qs.filter(store=store)
            product_qs = product_qs.filter(store=store)
            cart_item_qs = cart_item_qs.filter(product__store=store)
            wishlist_qs = wishlist_qs.filter(product__store=store)
            support_ticket_qs = support_ticket_qs.filter(store=store)

        summary = {
            'totalOrders': order_qs.count(),
            'totalProducts': product_qs.count(),
            'totalCartItems': cart_item_qs.count(),
            'totalWishlistItems': wishlist_qs.count(),
            'totalSupportTickets': support_ticket_qs.count(),
        }

        # Build time-series across all entities keyed by bucket label.
        series_map: dict[str, dict] = {}

        def _update_series(qs, key: str):
            for row in qs.annotate(
                period=bucket_func('created_at')
            ).values('period').annotate(total=Count('id')).order_by('period'):
                period = row['period']
                if period is None:
                    continue
                # Normalize to ISO date string for frontend.
                label = getattr(period, 'date', lambda: period)()
                label_str = label.isoformat()
                entry = series_map.setdefault(
                    label_str,
                    {
                        'label': label_str,
                        'orders': 0,
                        'products': 0,
                        'cartItems': 0,
                        'wishlistItems': 0,
                        'supportTickets': 0,
                    },
                )
                entry[key] = row['total']

        _update_series(order_qs, 'orders')
        _update_series(product_qs, 'products')
        _update_series(cart_item_qs, 'cartItems')
        _update_series(wishlist_qs, 'wishlistItems')
        _update_series(support_ticket_qs, 'supportTickets')

        # Sort existing buckets.
        series = sorted(series_map.values(), key=lambda x: x['label'])

        # Ensure full date coverage for day bucket by filling gaps with zeros.
        if (bucket or "day").lower() == "day":
            filled_series = []
            current = start_date
            end_inclusive = end_date
            by_label = {entry['label']: entry for entry in series}

            while current <= end_inclusive:
                label_str = current.isoformat()
                entry = by_label.get(label_str)
                if not entry:
                    entry = {
                        'label': label_str,
                        'orders': 0,
                        'products': 0,
                        'cartItems': 0,
                        'wishlistItems': 0,
                        'supportTickets': 0,
                    }
                filled_series.append(entry)
                current += timedelta(days=1)

            series = filled_series

        return Response({
            'summary': summary,
            'series': series,
            'meta': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'bucket': bucket,
            },
        })


def _get_branding_response(request, store: Store):
    """Build branding JSON for API response from Store."""
    logo_url = None
    if store.logo:
        logo_url = request.build_absolute_uri(store.logo.url)
    return {
        'logo_url': logo_url,
        'admin_name': store.name or 'E-commerce Store',
        'owner_name': store.owner_name or '',
        'owner_email': store.owner_email or '',
        'currency_symbol': store.currency_symbol or '৳',
        'store_type': store.store_type or '',
        'contact_email': store.contact_email or '',
        'phone': store.phone or '',
        'address': store.address or '',
    }


class BrandingView(APIView):
    """
    GET/PATCH store branding. Requires an active store context.

    GET:  any dashboard user (IsDashboardUser)
    PATCH: store admin or owner only (IsStoreAdmin)
    """
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        if self.request.method in ("PATCH", "PUT"):
            return [IsStoreAdmin()]
        return [IsDashboardUser()]

    def _get_store(self, request):
        ctx = get_active_store(request)
        if not ctx.store:
            raise PermissionDenied(
                "No active store resolved. Send the X-Store-ID header or re-login."
            )
        return ctx.store

    def get(self, request):
        try:
            store = self._get_store(request)
        except PermissionDenied:
            return Response({
                'logo_url': None,
                'admin_name': 'E-commerce Store',
                'owner_name': '',
                'owner_email': '',
                'currency_symbol': '৳',
                'store_type': '',
                'contact_email': '',
                'phone': '',
                'address': '',
            })
        return Response(_get_branding_response(request, store))

    def patch(self, request):
        store = self._get_store(request)
        if 'admin_name' in request.data:
            val = request.data.get('admin_name', '').strip() or 'E-commerce Store'
            store.name = val
        if 'owner_name' in request.data:
            val = (request.data.get('owner_name') or '').strip()[:255]
            if val:
                store.owner_name = val
        # owner_email is read-only from the dashboard; only admins can change
        # it via Django admin (which syncs User.email → Store.owner_email).
        if 'currency_symbol' in request.data:
            store.currency_symbol = (request.data.get('currency_symbol') or '৳').strip()[:10]
        if 'store_type' in request.data:
            val = (request.data.get('store_type') or '').strip()[:60]
            if val and len(val.split()) > 4:
                return Response(
                    {'detail': 'store_type must be at most 4 words.'},
                    status=400,
                )
            store.store_type = val
        if 'contact_email' in request.data:
            store.contact_email = (request.data.get('contact_email') or '').strip()[:254]
        if 'phone' in request.data:
            store.phone = (request.data.get('phone') or '').strip()[:50]
        if 'address' in request.data:
            store.address = (request.data.get('address') or '').strip()
        logo_file = request.FILES.get('logo')
        if logo_file:
            store.logo = logo_file
        if request.data.get('clear_logo') in (True, 'true', '1'):
            store.logo = None
        # Save only non-auth fields; the post_save signal is responsible for
        # syncing owner_name to User — owner_email is NOT synced (see above).
        store.save()
        return Response(_get_branding_response(request, store))
