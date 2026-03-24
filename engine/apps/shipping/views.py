from decimal import Decimal

from rest_framework.response import Response
from rest_framework.views import APIView

from engine.core.tenancy import get_active_store

from .models import ShippingMethod, ShippingZone, ShippingRate
from .serializers import ShippingOptionSerializer


class ShippingOptionsView(APIView):
    """
    GET ?zone_public_id=szn_xxx&order_total=99.00
    Returns available shipping methods and estimated price for the given zone and order total.
    """
    def get(self, request):
        ctx = get_active_store(request)
        store = ctx.store
        if not store:
            return Response([], status=200)

        zone_public_id = (request.query_params.get("zone_public_id") or "").strip()
        if not zone_public_id:
            return Response({"detail": "zone_public_id is required."}, status=400)

        order_total = request.query_params.get('order_total')
        try:
            order_total = Decimal(order_total) if order_total else None
        except Exception:
            order_total = None

        zone = ShippingZone.objects.filter(
            store=store,
            is_active=True,
            public_id=zone_public_id,
        ).first()
        if zone is None:
            return Response([], status=200)

        methods = ShippingMethod.objects.filter(
            store=store,
            is_active=True,
        ).prefetch_related('rates__shipping_zone')
        methods = methods.distinct()

        options = []
        for method in methods:
            method_zone_ids = set(method.zones.values_list("id", flat=True))
            if method_zone_ids and zone.id not in method_zone_ids:
                continue
            for rate in method.rates.filter(store=store, is_active=True).select_related('shipping_zone'):
                if rate.shipping_zone_id != zone.id:
                    continue
                if order_total is not None:
                    if rate.min_order_total and order_total < rate.min_order_total:
                        continue
                    if rate.max_order_total and order_total > rate.max_order_total:
                        continue
                options.append({
                    'method_public_id': method.public_id,
                    'method_name': method.name,
                    'zone_public_id': rate.shipping_zone.public_id,
                    'zone_name': rate.shipping_zone.name,
                    'price': rate.price,
                    'rate_type': rate.rate_type,
                })
        return Response(ShippingOptionSerializer(options, many=True).data)
