from django.db import transaction
from django.db.models import Prefetch
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import CreateAPIView, RetrieveAPIView
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from config.permissions import DenyAPIKeyAccess, IsAdminUser, IsStorefrontAPIKey

from engine.apps.analytics.service import meta_conversions
from engine.core.tenancy import get_active_store, require_api_key_store

from .models import Order, OrderItem
from .order_financials import compute_line_financials
from .serializers import (
    OrderCreateSerializer,
    OrderSerializer,
    StorefrontOrderReceiptSerializer,
)
from .services import (
    build_variant_snapshot_text,
    recalculate_order_totals,
    resolve_and_attach_customer,
)
from .utils import get_next_order_number
from .stock import adjust_stock
from .throttles import DirectOrderRateThrottle
from engine.apps.emails.triggers import notify_store_new_order
from engine.core.realtime import emit_store_event


def _notify_order_created(order: Order) -> None:
    notify_store_new_order(order)
    emit_store_event(
        order.store.public_id,
        "payment_success",
        {"order_public_id": order.public_id},
    )


class OrderCreateView(CreateAPIView):
    """Create order from request body line items (stateless checkout)."""
    serializer_class = OrderCreateSerializer
    authentication_classes = []
    throttle_classes = [DirectOrderRateThrottle, UserRateThrottle]
    allow_api_key = True

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAdminUser(), DenyAPIKeyAccess()]
        return [IsStorefrontAPIKey()]

    def get(self, request, *args, **kwargs):
        ctx = get_active_store(request)
        store = ctx.store
        if not store:
            raise PermissionDenied("No active store resolved.")
        queryset = Order.objects.filter(store=store).prefetch_related(
            "items__product", "items__product__images"
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = OrderSerializer(page, many=True, context={"request": request})
            return self.get_paginated_response(serializer.data)
        data = OrderSerializer(queryset, many=True, context={"request": request}).data
        return Response(data, status=status.HTTP_200_OK)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        allowed_top_level_fields = {
            "shipping_zone_public_id",
            "shipping_method_public_id",
            "shipping_name",
            "phone",
            "email",
            "shipping_address",
            "district",
            "products",
        }
        unknown_fields = set(request.data.keys()) - allowed_top_level_fields
        if unknown_fields:
            return Response(
                {"detail": f"Unknown fields are not allowed: {', '.join(sorted(unknown_fields))}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        products_data = request.data.get("products") or []
        if not isinstance(products_data, list) or not products_data:
            return Response(
                {'detail': 'No products provided.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        request_store = require_api_key_store(request)

        from engine.apps.products.models import Product, ProductVariant
        from engine.apps.inventory.models import Inventory
        from engine.apps.products.variant_utils import unit_price_for_line

        product_public_ids = [p["product_public_id"] for p in products_data]
        variant_public_ids = [
            (p.get("variant_public_id") or "").strip()
            for p in products_data
            if (p.get("variant_public_id") or "").strip()
        ]

        locked_products = {
            p.public_id: p
            for p in Product.objects.filter(
                public_id__in=product_public_ids,
                store=request_store,
                is_active=True,
                status=Product.Status.ACTIVE,
            ).select_for_update()
        }
        locked_variants = {
            v.public_id: v
            for v in ProductVariant.objects.filter(
                public_id__in=variant_public_ids,
                product__store=request_store,
                product__is_active=True,
                product__status=Product.Status.ACTIVE,
                is_active=True,
            )
            .select_for_update()
            .select_related("product")
        }
        locked_product_inventory = {
            inv.product_id: inv
            for inv in Inventory.objects.select_for_update().filter(
                product_id__in=[p.id for p in locked_products.values()],
                variant__isnull=True,
                product__store=request_store,
            )
        }
        locked_variant_inventory = {
            inv.variant_id: inv
            for inv in Inventory.objects.select_for_update().filter(
                variant_id__in=[v.id for v in locked_variants.values()],
                product__store=request_store,
            )
        }

        store_ids = {p.store_id for p in locked_products.values() if p.store_id}
        if len(store_ids) > 1:
            return Response(
                {"detail": "All products in a single order must belong to the same store."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not locked_products:
            return Response(
                {"detail": "No valid products provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        first_product = next(iter(locked_products.values()))
        order_store = first_product.store
        if order_store.id != request_store.id:
            return Response(
                {"detail": "Store mismatch for this order request."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = self.get_serializer(
            data=request.data,
            context={**self.get_serializer_context(), "store": order_store},
        )
        ser.is_valid(raise_exception=True)
        products_data = ser.validated_data["products"]

        stock_errors = []
        for product_data in products_data:
            product_id_str = product_data["product_public_id"]
            quantity = product_data["quantity"]
            product = locked_products.get(product_id_str)
            if not product:
                stock_errors.append(f"Product {product_id_str} not found.")
                continue
            vpid = (product_data.get("variant_public_id") or "").strip()
            if vpid:
                variant = locked_variants.get(vpid)
                if not variant or variant.product_id != product.id:
                    stock_errors.append(f"Variant unavailable for {product.name}.")
                    continue
                inv = locked_variant_inventory.get(variant.id)
                available = int(inv.quantity) if inv else 0
                if available < quantity:
                    stock_errors.append(
                        f"Insufficient variant stock for {product.name}. "
                        f"Available: {available}, Requested: {quantity}"
                    )
            else:
                inv = locked_product_inventory.get(product.id)
                available = int(inv.quantity) if inv else 0
                if available < quantity:
                    stock_errors.append(
                        f"Insufficient stock for {product.name}. "
                        f"Available: {available}, Requested: {quantity}"
                    )

        if stock_errors:
            return Response(
                {"detail": "Stock validation failed.", "errors": stock_errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = (ser.validated_data.get('email') or '').strip()
        if not email and request.user.is_authenticated:
            email = (getattr(request.user, 'email', '') or '').strip()

        district = (ser.validated_data.get('district') or '').strip()

        order = Order.objects.create(
            store=order_store,
            order_number=get_next_order_number(order_store),
            status=Order.Status.PENDING,
            user=request.user if request.user.is_authenticated else None,
            email=email,
            shipping_name=ser.validated_data['shipping_name'],
            shipping_address=ser.validated_data['shipping_address'],
            phone=ser.validated_data['phone'],
            district=district,
            shipping_zone=ser.validated_data["shipping_zone"],
            shipping_method=ser.validated_data.get("shipping_method"),
        )

        for product_data in products_data:
            product_id_str = product_data["product_public_id"]
            quantity = product_data["quantity"]
            product = locked_products[product_id_str]
            vpid = (product_data.get("variant_public_id") or "").strip()
            variant = locked_variants.get(vpid) if vpid else None
            unit = unit_price_for_line(product, variant)
            fin = compute_line_financials(
                product=product,
                variant=variant,
                quantity=quantity,
                unit_price=unit,
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                variant=variant,
                product_name_snapshot=product.name,
                variant_snapshot=build_variant_snapshot_text(variant),
                unit_price_snapshot=fin["unit_price"],
                quantity=quantity,
                **fin,
            )
            try:
                adjust_stock(
                    product_id=product.id,
                    variant_id=variant.id if variant else None,
                    delta_qty=quantity,
                    store_id=order_store.id,
                )
            except DjangoValidationError as e:
                return Response(
                    {"detail": "Stock validation failed.", "errors": e.message_dict if hasattr(e, "message_dict") else str(e)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        recalculate_order_totals(order)
        resolve_and_attach_customer(
            order,
            store=order_store,
            name=order.shipping_name,
            phone=order.phone,
            email=order.email,
            address=order.shipping_address,
        )

        meta_conversions.track_add_payment_info(request, {
            'email': order.email,
            'phone': order.phone,
            'shipping_name': order.shipping_name,
        })
        meta_conversions.track_purchase(request, order)

        _notify_order_created(order)

        order_for_receipt = (
            Order.objects.filter(pk=order.pk)
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=OrderItem.objects.select_related("product", "variant").prefetch_related(
                        "variant__attribute_values__attribute_value__attribute",
                    ),
                )
            )
            .get()
        )
        return Response(
            StorefrontOrderReceiptSerializer(instance=order_for_receipt).data,
            status=status.HTTP_201_CREATED,
        )


class OrderDetailView(RetrieveAPIView):
    """Get order by public_id (dashboard staff). Not available with publishable API key."""
    serializer_class = OrderSerializer
    queryset = Order.objects.select_related(
        "shipping_zone", "shipping_method", "shipping_rate", "customer"
    ).prefetch_related(
        'items__product',
        'items__product__images',
        'items__variant__attribute_values__attribute_value__attribute',
    )
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"
    permission_classes = [IsAdminUser, DenyAPIKeyAccess]

    def get_object(self):
        public_id = self.kwargs.get(self.lookup_url_kwarg)
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise PermissionDenied("No active store resolved.")
        order = self.get_queryset().filter(public_id=public_id, store=store).first()
        if not order:
            raise NotFound()
        return order


class InitiateCheckoutView(APIView):
    """
    Signal the start of the checkout flow.
    Called by the frontend when the user navigates to the checkout page.
    Fires an InitiateCheckout event to Meta Conversions API and returns 200.
    """
    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True

    def post(self, request):
        meta_conversions.track_initiate_checkout(request)
        return Response({'status': 'ok'})
