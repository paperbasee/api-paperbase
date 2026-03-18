from decimal import Decimal
from datetime import date, timedelta

from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from config.permissions import IsDashboardUser
from engine.apps.billing.feature_gate import require_feature
from engine.core.tenancy import get_active_store
from engine.apps.stores.models import Store
from engine.apps.orders.models import Order
from engine.apps.orders.admin_serializers import AdminOrderListSerializer
from engine.apps.products.models import Product, Category, Brand
from engine.apps.support.models import ContactSubmission
from engine.apps.notifications.models import Notification
from engine.apps.cart.models import Cart, CartItem
from engine.apps.wishlist.models import WishlistItem


class DashboardStatsView(APIView):
    permission_classes = [IsDashboardUser]

    def get(self, request):
        order_counts = Order.objects.aggregate(
            total_count=Count('id'),
            pending_count=Count('id', filter=Q(status=Order.Status.PENDING)),
            confirmed_count=Count('id', filter=Q(status=Order.Status.CONFIRMED)),
            cancelled_count=Count('id', filter=Q(status=Order.Status.CANCELLED)),
        )
        revenue_agg = Order.objects.exclude(
            status=Order.Status.CANCELLED,
        ).aggregate(revenue=Sum('total'))

        product_stats = Product.objects.aggregate(
            total_count=Count('id'),
            active_count=Count('id', filter=Q(is_active=True)),
            oos_count=Count('id', filter=Q(stock=0, is_active=True)),
        )

        recent_orders = Order.objects.prefetch_related('items__product')[:10]

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
            'categories': Category.objects.filter(parent__isnull=True).count(),
            'subcategories': Category.objects.filter(parent__isnull=False).count(),
            'brands': Brand.objects.count(),
            'contacts': ContactSubmission.objects.count(),
            'notifications': Notification.objects.filter(is_active=True).count(),
            # Count only carts that actually have at least one item
            'carts': Cart.objects.filter(items__isnull=False).distinct().count(),
            'wishlist': WishlistItem.objects.count(),
            'recent_orders': AdminOrderListSerializer(recent_orders, many=True).data,
        })


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
        require_feature(request.user, "advanced_analytics")
        start_date, end_date = self._parse_date_range(request)
        bucket = request.query_params.get('bucket', 'day')
        bucket_func = self._get_bucket_func(bucket)

        # Filter base querysets by date range (inclusive).
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
        contact_qs = ContactSubmission.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        summary = {
            'totalOrders': order_qs.count(),
            'totalProducts': product_qs.count(),
            'totalCartItems': cart_item_qs.count(),
            'totalWishlistItems': wishlist_qs.count(),
            'totalContacts': contact_qs.count(),
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
                        'contacts': 0,
                    },
                )
                entry[key] = row['total']

        _update_series(order_qs, 'orders')
        _update_series(product_qs, 'products')
        _update_series(cart_item_qs, 'cartItems')
        _update_series(wishlist_qs, 'wishlistItems')
        _update_series(contact_qs, 'contacts')

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
                        'contacts': 0,
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
    """GET/PATCH branding from Store. Uses active store (X-Store-ID or host) or first store as fallback."""
    permission_classes = [IsDashboardUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _get_store(self, request):
        ctx = get_active_store(request)
        if ctx.store:
            return ctx.store
        return Store.objects.filter(is_active=True).first()

    def get(self, request):
        store = self._get_store(request)
        if not store:
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
        if not store:
            return Response(
                {'detail': 'No store available. Create a store first.'},
                status=400,
            )
        if 'admin_name' in request.data:
            val = request.data.get('admin_name', '').strip() or 'E-commerce Store'
            store.name = val
        if 'owner_name' in request.data:
            val = (request.data.get('owner_name') or '').strip()[:255]
            if val:
                store.owner_name = val
        if 'owner_email' in request.data:
            val = (request.data.get('owner_email') or '').strip()[:254]
            if val and '@' in val:
                store.owner_email = val
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
        store.save()
        return Response(_get_branding_response(request, store))
