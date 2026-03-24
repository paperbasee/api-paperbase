from rest_framework import serializers

from engine.apps.products.models import Product
from engine.core.tenancy import get_active_store

from .models import Review


class ReviewSerializer(serializers.ModelSerializer):
    product_public_id = serializers.CharField(source='product.public_id', read_only=True)
    user_public_id = serializers.CharField(source='user.public_id', read_only=True)

    class Meta:
        model = Review
        fields = ['public_id', 'product_public_id', 'user_public_id', 'rating', 'title', 'body', 'status', 'created_at']
        read_only_fields = ['public_id', 'product_public_id', 'user_public_id', 'status']


class ReviewCreateSerializer(serializers.ModelSerializer):
    product = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=Product.objects.none(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        ctx = get_active_store(request) if request else None
        if ctx and ctx.store:
            self.fields["product"].queryset = Product.objects.filter(
                is_active=True,
                status=Product.Status.ACTIVE,
                store=ctx.store,
            )
        else:
            self.fields["product"].queryset = Product.objects.none()

    class Meta:
        model = Review
        fields = ['product', 'rating', 'title', 'body']

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError('Rating must be between 1 and 5.')
        return value
