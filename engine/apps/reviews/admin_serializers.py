from rest_framework import serializers

from .models import Review


class AdminReviewSerializer(serializers.ModelSerializer):
    product_public_id = serializers.CharField(source="product.public_id", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    user_public_id = serializers.CharField(source="user.public_id", read_only=True)
    user_email = serializers.CharField(source="user.email", read_only=True)

    class Meta:
        model = Review
        fields = [
            "public_id",
            "product_public_id",
            "product_name",
            "user_public_id",
            "user_email",
            "rating",
            "title",
            "body",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "product_public_id", "product_name", "user_public_id", "user_email", "created_at", "updated_at"]
