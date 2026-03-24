from django.db.models import Avg, Count
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView, CreateAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from engine.core.tenancy import get_active_store

from .models import Review
from .serializers import ReviewSerializer, ReviewCreateSerializer
from engine.apps.products.models import Product


class ReviewListByProductView(ListAPIView):
    """List approved reviews for a product. GET /api/v1/reviews/?product_public_id=<public_id>"""
    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        ctx = get_active_store(self.request)
        if not ctx.store:
            return Review.objects.none()
        product_public_id = self.request.query_params.get('product_public_id')
        if not product_public_id:
            return Review.objects.none()
        return Review.objects.filter(
            product__public_id=product_public_id,
            product__store=ctx.store,
            status=Review.Status.APPROVED,
        ).select_related('user').order_by('-created_at')


class ReviewCreateView(CreateAPIView):
    """Create a review (authenticated)."""
    serializer_class = ReviewCreateSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ReviewRatingSummaryView(APIView):
    """GET /api/v1/reviews/summary/?product_public_id=<public_id> -> { average_rating, count }"""
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request):
        ctx = get_active_store(request)
        if not ctx.store:
            raise NotFound()
        product_public_id = request.query_params.get('product_public_id')
        if not product_public_id:
            return Response({'average_rating': None, 'count': 0})
        product_exists = Product.objects.filter(
            public_id=product_public_id,
            store=ctx.store,
            is_active=True,
            status=Product.Status.ACTIVE,
        ).exists()
        if not product_exists:
            raise NotFound()
        agg = Review.objects.filter(
            product__public_id=product_public_id,
            product__store=ctx.store,
            status=Review.Status.APPROVED,
        ).aggregate(avg=Avg('rating'), count=Count('id'))
        return Response({
            'average_rating': round(agg['avg'], 2) if agg['avg'] is not None else None,
            'count': agg['count'] or 0,
        })
