from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import CreateAPIView, ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from rest_framework.permissions import IsAuthenticated

from engine.apps.cart.views import get_or_create_cart
from engine.apps.analytics.service import meta_conversions

from .models import Order, OrderItem
from .serializers import OrderCreateSerializer, OrderSerializer, DirectOrderCreateSerializer
from .utils import get_next_order_number
from .stock import adjust_stock
from .throttles import DirectOrderRateThrottle
from engine.apps.shipping.service import quote_shipping


class OrderCreateView(CreateAPIView):
    """Create order from current cart."""
    serializer_class = OrderCreateSerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cart = get_or_create_cart(request)
        items = list(cart.items.select_related('product', 'variant'))
        if not items:
            return Response(
                {'detail': 'Cart is empty.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Lock products and variants for atomic stock validation.
        from engine.apps.products.models import Product, ProductVariant
        product_ids = [ci.product_id for ci in items]
        variant_ids = [ci.variant_id for ci in items if getattr(ci, "variant_id", None)]
        locked_products = {
            p.id: p for p in Product.objects.filter(id__in=product_ids).select_for_update()
        }
        locked_variants = {
            v.id: v
            for v in ProductVariant.objects.filter(id__in=variant_ids)
            .select_for_update()
            .select_related("product")
        }
        
        # Check stock availability (variant stock when variant is present).
        stock_errors = []
        for ci in items:
            if getattr(ci, "variant_id", None):
                variant = locked_variants.get(ci.variant_id)
                if not variant:
                    stock_errors.append(f"Variant {ci.variant_id} not found.")
                    continue
                if variant.stock_quantity < ci.quantity:
                    stock_errors.append(
                        f"Insufficient variant stock for {variant.product.name}. "
                        f"Available: {variant.stock_quantity}, Requested: {ci.quantity}"
                    )
            else:
                product = locked_products.get(ci.product_id)
                if not product:
                    stock_errors.append(f"Product {ci.product.name} not found.")
                    continue
                if product.stock < ci.quantity:
                    stock_errors.append(
                        f"Insufficient stock for {product.name}. "
                        f"Available: {product.stock}, Requested: {ci.quantity}"
                    )
        
        if stock_errors:
            return Response(
                {'detail': 'Stock validation failed.', 'errors': stock_errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create order and reduce stock
        subtotal = Decimal('0.00')
        # Cart has no direct store FK; derive store from the first cart item's product.
        store = items[0].product.store if items else None
        if not store:
            return Response(
                {"detail": "No store found for this cart."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        order = Order.objects.create(
            store=store,
            order_number=get_next_order_number(store),
            user=request.user if request.user.is_authenticated else None,
            email=ser.validated_data['email'],
            shipping_name=ser.validated_data['shipping_name'],
            shipping_address=ser.validated_data['shipping_address'],
        )
        for ci in items:
            product = locked_products[ci.product_id]
            variant = locked_variants.get(ci.variant_id) if getattr(ci, "variant_id", None) else None
            price = (
                getattr(variant, "price_override", None) or product.price
                if variant is not None
                else product.price
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                variant=variant,
                quantity=ci.quantity,
                price=price
            )
            try:
                adjust_stock(product_id=product.id, variant_id=variant.id if variant else None, delta_qty=ci.quantity)
            except DjangoValidationError as e:
                return Response(
                    {"detail": "Stock validation failed.", "errors": e.message_dict if hasattr(e, "message_dict") else str(e)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            subtotal += price * ci.quantity

        quote = quote_shipping(
            store=order.store,
            order_subtotal=subtotal,
            delivery_area=(order.delivery_area or "").strip().lower() or None,
            district=(order.district or "").strip() or None,
        )
        order.subtotal = subtotal
        order.shipping_cost = quote.shipping_cost
        order.shipping_zone = quote.zone
        order.shipping_method = quote.method
        order.shipping_rate = quote.rate
        order.total = subtotal + quote.shipping_cost
        order.save(
            update_fields=[
                "subtotal",
                "shipping_cost",
                "shipping_zone",
                "shipping_method",
                "shipping_rate",
                "total",
            ]
        )
        cart.items.all().delete()

        meta_conversions.track_purchase(request, order)

        return Response(
            OrderSerializer(instance=order, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )


class DirectOrderCreateView(CreateAPIView):
    """Create order directly with products (not from cart)."""
    serializer_class = DirectOrderCreateSerializer
    permission_classes = []  # Allow unauthenticated (storefront) access
    authentication_classes = []
    throttle_classes = [DirectOrderRateThrottle]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        
        products_data = ser.validated_data['products']
        if not products_data:
            return Response(
                {'detail': 'No products provided.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get products with locked rows for atomic stock updates
        from engine.apps.products.models import Product
        product_public_ids = [p['public_id'] for p in products_data]

        locked_products = {
            p.public_id: p
            for p in Product.objects.filter(public_id__in=product_public_ids).select_for_update()
        }

        # Validate all products belong to the same store.
        store_ids = {p.store_id for p in locked_products.values() if p.store_id}
        if len(store_ids) > 1:
            return Response(
                {'detail': 'All products in a single order must belong to the same store.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check stock availability
        stock_errors = []
        for product_data in products_data:
            product_id_str = product_data['public_id']
            quantity = product_data['quantity']
            product = locked_products.get(product_id_str)
            if not product:
                stock_errors.append(f"Product {product_id_str} not found.")
                continue
            if product.stock < quantity:
                stock_errors.append(
                    f"Insufficient stock for {product.name}. "
                    f"Available: {product.stock}, Requested: {quantity}"
                )

        if stock_errors:
            return Response(
                {'detail': 'Stock validation failed.', 'errors': stock_errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Email: use form-provided value, fall back to authenticated user's email.
        email = (ser.validated_data.get('email') or '').strip()
        if not email and request.user.is_authenticated:
            email = (getattr(request.user, 'email', '') or '').strip()

        # Derive delivery_area server-side from district for consistency.
        district = (ser.validated_data.get('district') or '').strip()
        if district:
            delivery_area = 'inside' if district == 'Dhaka' else 'outside'
        else:
            delivery_area = ser.validated_data['delivery_area']

        # Resolve store from the (now validated) locked products set.
        first_product = next(iter(locked_products.values()))
        order_store = first_product.store

        # Create order and reduce stock
        subtotal = Decimal('0.00')
        order = Order.objects.create(
            store=order_store,
            order_number=get_next_order_number(order_store),
            user=request.user if request.user.is_authenticated else None,
            email=email,
            shipping_name=ser.validated_data['shipping_name'],
            shipping_address=ser.validated_data['shipping_address'],
            phone=ser.validated_data['phone'],
            district=district,
            delivery_area=delivery_area,
        )
        
        for product_data in products_data:
            product_id_str = product_data['public_id']
            quantity = product_data['quantity']
            product = locked_products[product_id_str]
            price = product.price
            OrderItem.objects.create(
                order=order, product=product, quantity=quantity,
                price=price
            )
            try:
                adjust_stock(product_id=product.id, variant_id=None, delta_qty=quantity)
            except DjangoValidationError as e:
                return Response(
                    {"detail": "Stock validation failed.", "errors": e.message_dict if hasattr(e, "message_dict") else str(e)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            subtotal += price * quantity
        
        # Add shipping cost from dynamic shipping rules (store-scoped).
        quote = quote_shipping(
            store=order.store,
            order_subtotal=subtotal,
            delivery_area=delivery_area,
            district=district or None,
        )
        order.subtotal = subtotal
        order.shipping_cost = quote.shipping_cost
        order.shipping_zone = quote.zone
        order.shipping_method = quote.method
        order.shipping_rate = quote.rate
        order.total = subtotal + quote.shipping_cost
        order.save(
            update_fields=[
                "subtotal",
                "shipping_cost",
                "shipping_zone",
                "shipping_method",
                "shipping_rate",
                "total",
            ]
        )

        meta_conversions.track_add_payment_info(request, {
            'email': order.email,
            'phone': order.phone,
            'shipping_name': order.shipping_name,
        })
        meta_conversions.track_purchase(request, order)

        return Response(
            OrderSerializer(instance=order, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )


class OrderListView(ListAPIView):
    """List orders for the authenticated user."""
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return Order.objects.none()
        return Order.objects.filter(user=self.request.user).prefetch_related(
            'items__product', 'items__product__images'
        )


class OrderDetailView(RetrieveAPIView):
    """Get order by id (for track-order). Allow by id + email for guests."""
    serializer_class = OrderSerializer
    queryset = Order.objects.prefetch_related('items__product', 'items__product__images')

    def get_object(self):
        order_id = self.kwargs.get('id')
        order = self.get_queryset().filter(order_number=order_id).first()
        if not order:
            raise NotFound()
        if order.user_id and (not self.request.user.is_authenticated or order.user_id != self.request.user.id):
            raise NotFound()
        if not order.user_id:
            email = self.request.query_params.get('email', '').strip().lower()
            if not email or order.email.lower() != email:
                raise NotFound()
        return order


class InitiateCheckoutView(APIView):
    """
    Signal the start of the checkout flow.
    Called by the frontend when the user navigates to the checkout page.
    Fires an InitiateCheckout event to Meta Conversions API and returns 200.
    """
    permission_classes = []
    authentication_classes = []

    def post(self, request):
        meta_conversions.track_initiate_checkout(request)
        return Response({'status': 'ok'})
