from decimal import Decimal

from django.db.models import Sum, Count, Q
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from rest_framework.exceptions import PermissionDenied

from config.permissions import DenyAPIKeyAccess, IsAdminUser, IsStoreAdmin
from engine.core.request_context import (
    get_branding_request_cache,
    get_dashboard_store_from_request,
)
from engine.core.tenant_drf import ProvenTenantContextMixin
from engine.apps.stores.models import Store, StoreSettings
from engine.apps.stores.services import (
    get_request_store_settings_row,
    set_request_store_settings_row,
)
from engine.apps.stores.social_links import (
    coerce_social_links_patch,
    default_social_links,
    normalize_social_links_from_storefront_public,
)
from engine.apps.orders.models import Order
from engine.apps.orders.admin_serializers import AdminOrderListSerializer
from engine.apps.products.models import Product, Category
from engine.apps.support.models import SupportTicket
from engine.apps.notifications.models import StorefrontCTA


class DashboardStatsView(ProvenTenantContextMixin, APIView):
    permission_classes = [DenyAPIKeyAccess, IsAdminUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "standard_api"

    def get(self, request):
        store = get_dashboard_store_from_request(request)
        if not store:
            raise PermissionDenied("No active store resolved.")

        order_qs = Order.objects.all()
        product_qs = Product.objects.all()
        category_qs = Category.objects.all()
        support_ticket_qs = SupportTicket.objects.all()
        notification_qs = StorefrontCTA.objects.filter(is_active=True)
        order_qs = order_qs.filter(store=store)
        product_qs = product_qs.filter(store=store)
        category_qs = category_qs.filter(store=store)
        support_ticket_qs = support_ticket_qs.filter(store=store)
        notification_qs = notification_qs.filter(store=store)

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

        recent_orders = (
            order_qs.annotate(items_count=Count("items"))
            .select_related("customer")
            .order_by("-created_at")[:10]
        )

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
            'category_roots': category_qs.filter(parent__isnull=True).count(),
            'category_total': category_qs.count(),
            'support_tickets': support_ticket_qs.count(),
            'notifications': notification_qs.count(),
            'recent_orders': AdminOrderListSerializer(recent_orders, many=True).data,
        })


def _get_branding_response(request, store: Store):
    """Build branding JSON for API response from Store."""
    branding = get_branding_request_cache()
    cached = branding.get(store.public_id)
    if cached is not None:
        return cached

    logo_url = None
    if store.logo:
        logo_url = request.build_absolute_uri(store.logo.url)
    settings_row = get_request_store_settings_row(request, store)
    storefront_public = (settings_row.storefront_public or {}) if settings_row else {}
    social_links = normalize_social_links_from_storefront_public(storefront_public)
    data = {
        'logo_url': logo_url,
        'admin_name': store.name or 'E-commerce Store',
        'owner_name': store.owner_name or '',
        'owner_email': store.owner_email or '',
        'currency_symbol': store.currency_symbol or '৳',
        'store_type': store.store_type or '',
        'contact_email': store.contact_email or '',
        'phone': store.phone or '',
        'address': store.address or '',
        'social_links': social_links,
    }
    branding[store.public_id] = data
    return data


class BrandingView(APIView):
    """
    GET/PATCH store branding. Requires an active store context.

    GET:  any dashboard user (IsDashboardUser)
    PATCH: store admin or owner only (IsStoreAdmin)
    """
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        if self.request.method in ("PATCH", "PUT"):
            return [DenyAPIKeyAccess(), IsStoreAdmin()]
        return [DenyAPIKeyAccess(), IsAdminUser()]

    def _get_store(self, request):
        store = get_dashboard_store_from_request(request)
        if not store:
            raise PermissionDenied(
                "No active store resolved. Send the X-Store-ID header or re-login."
            )
        return store

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
                'social_links': default_social_links(),
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

        if "social_links" in request.data:
            try:
                merged_sl = coerce_social_links_patch(request.data.get("social_links"))
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=400)
            settings_obj, _ = StoreSettings.objects.get_or_create(store=store)
            fp = dict(settings_obj.storefront_public or {})
            fp["social_links"] = merged_sl
            settings_obj.storefront_public = fp
            settings_obj.save(update_fields=["storefront_public", "updated_at"])
            set_request_store_settings_row(request, store, settings_obj)

        get_branding_request_cache().pop(store.public_id, None)
        return Response(_get_branding_response(request, store))
