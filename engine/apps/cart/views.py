from rest_framework import status
from rest_framework.generics import RetrieveAPIView
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from engine.apps.analytics.service import meta_conversions
from engine.apps.products.models import Product
from engine.core.tenancy import get_active_store

from .models import Cart, CartItem
from .serializers import CartAddSerializer, CartItemSerializer, CartSerializer


def get_or_create_cart(request):
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user, defaults={'session_key': ''})
    else:
        if not request.session.session_key:
            request.session.create()
        cart, _ = Cart.objects.get_or_create(
            user=None, session_key=request.session.session_key
        )
    return cart


class CartDetailView(RetrieveAPIView):
    """Get current cart with items."""
    permission_classes = [AllowAny]
    serializer_class = CartSerializer

    def get_object(self):
        cart = get_or_create_cart(self.request)
        return Cart.objects.prefetch_related(
            'items__product', 'items__product__images'
        ).get(pk=cart.pk)


class CartAddView(APIView):
    """Add or update item in cart."""
    permission_classes = [AllowAny]

    def post(self, request):
        ctx = get_active_store(request)
        if not ctx.store:
            raise NotFound()
        ser = CartAddSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        cart = get_or_create_cart(request)
        product = Product.objects.filter(
            public_id=ser.validated_data['product_public_id'],
            store=ctx.store,
            is_active=True,
            status=Product.Status.ACTIVE,
        ).first()
        if not product:
            raise NotFound()
        quantity = ser.validated_data['quantity']
        size = (ser.validated_data.get('size') or '').strip()

        item, created = CartItem.objects.update_or_create(
            cart=cart, product=product, size=size,
            defaults={'quantity': quantity}
        )
        if not created:
            item.quantity = quantity
            item.save(update_fields=['quantity', 'updated_at'])

        meta_conversions.track_add_to_cart(request, product, quantity)

        return Response(
            CartItemSerializer(instance=item, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )


class CartUpdateView(APIView):
    """Update quantity of a cart item."""
    permission_classes = [AllowAny]

    def patch(self, request, item_public_id):
        cart = get_or_create_cart(request)
        quantity = request.data.get('quantity')
        if quantity is None or not isinstance(quantity, int) or quantity < 1:
            return Response({'quantity': ['Must be a positive integer.']}, status=400)
        item = CartItem.objects.filter(cart=cart, public_id=item_public_id).first()
        if not item:
            return Response({'detail': 'Not found.'}, status=404)
        item.quantity = quantity
        item.save(update_fields=['quantity', 'updated_at'])
        return Response(CartItemSerializer(instance=item, context={'request': request}).data)


class CartRemoveView(APIView):
    """Remove item from cart."""
    permission_classes = [AllowAny]

    def post(self, request, item_public_id):
        cart = get_or_create_cart(request)
        deleted, _ = CartItem.objects.filter(cart=cart, public_id=item_public_id).delete()
        return Response({'status': 'removed', 'deleted': deleted > 0})


class CartRemoveByProductView(APIView):
    """Remove a cart item by product public_id (used by frontend sync)."""
    permission_classes = [AllowAny]

    def post(self, request, product_public_id):
        ctx = get_active_store(request)
        if not ctx.store:
            raise NotFound()
        cart = get_or_create_cart(request)
        product = Product.objects.filter(
            public_id=product_public_id,
            store=ctx.store,
            is_active=True,
            status=Product.Status.ACTIVE,
        ).first()
        if not product:
            raise NotFound()
        deleted, _ = CartItem.objects.filter(cart=cart, product=product).delete()
        return Response({'status': 'removed', 'deleted': deleted > 0})


class CartClearView(APIView):
    """Remove all items from the current cart."""
    permission_classes = [AllowAny]

    def post(self, request):
        cart = get_or_create_cart(request)
        deleted, _ = cart.items.all().delete()
        return Response({'status': 'cleared', 'deleted': deleted})
