from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from engine.apps.analytics.service import meta_conversions
from engine.apps.products.models import Product
from engine.core.tenancy import get_active_store

from .models import WishlistItem
from .serializers import WishlistAddSerializer, WishlistItemSerializer


def _wishlist_filter(request):
    """Return a dict of kwargs to filter WishlistItem for the current visitor."""
    if request.user.is_authenticated:
        return {'user': request.user}
    if not request.session.session_key:
        request.session.create()
    return {'user': None, 'session_key': request.session.session_key}


class WishlistListView(ListAPIView):
    """List current visitor's wishlist items."""
    serializer_class = WishlistItemSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return WishlistItem.objects.filter(
            **_wishlist_filter(self.request)
        ).select_related('product').prefetch_related('product__images')


class WishlistAddView(APIView):
    """Add product to wishlist. Idempotent."""
    permission_classes = [AllowAny]

    def post(self, request):
        ctx = get_active_store(request)
        if not ctx.store:
            raise NotFound()
        ser = WishlistAddSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        product = Product.objects.filter(
            public_id=ser.validated_data['product_public_id'],
            store=ctx.store,
            is_active=True,
            status=Product.Status.ACTIVE,
        ).first()
        if not product:
            raise NotFound()
        filt = _wishlist_filter(request)
        _, created = WishlistItem.objects.get_or_create(product=product, **filt)
        if created:
            meta_conversions.track_add_to_wishlist(request, product)
        return Response(
            {'status': 'added', 'created': created},
            status=status.HTTP_201_CREATED,
        )


class WishlistRemoveView(APIView):
    """Remove product from wishlist."""
    permission_classes = [AllowAny]

    def post(self, request, product_public_id):
        ctx = get_active_store(request)
        if not ctx.store:
            raise NotFound()
        product = Product.objects.filter(
            public_id=product_public_id,
            store=ctx.store,
            is_active=True,
            status=Product.Status.ACTIVE,
        ).first()
        if not product:
            raise NotFound()
        deleted, _ = WishlistItem.objects.filter(
            product=product, **_wishlist_filter(request)
        ).delete()
        return Response({'status': 'removed', 'deleted': deleted > 0})


class WishlistClearView(APIView):
    """Remove all items from the current visitor's wishlist."""
    permission_classes = [AllowAny]

    def post(self, request):
        deleted, _ = WishlistItem.objects.filter(
            **_wishlist_filter(request)
        ).delete()
        return Response({'status': 'cleared', 'deleted': deleted})
