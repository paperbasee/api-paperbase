from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from engine.apps.products.models import Category, Product

from .models import BulkDiscount, Coupon
from .services import get_coupon_usage_stats


class AdminCouponSerializer(serializers.ModelSerializer):
    successful_uses = serializers.SerializerMethodField()
    reversed_uses = serializers.SerializerMethodField()

    class Meta:
        model = Coupon
        fields = [
            "public_id",
            "code",
            "discount_type",
            "discount_value",
            "min_order_value",
            "max_uses",
            "per_user_max_uses",
            "times_used",
            "successful_uses",
            "reversed_uses",
            "valid_from",
            "valid_until",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "public_id",
            "times_used",
            "successful_uses",
            "reversed_uses",
            "created_at",
            "updated_at",
        ]

    def get_successful_uses(self, obj):
        return get_coupon_usage_stats(store=obj.store, coupon=obj)["successful_uses"]

    def get_reversed_uses(self, obj):
        return get_coupon_usage_stats(store=obj.store, coupon=obj)["reversed_uses"]


class AdminBulkDiscountSerializer(serializers.ModelSerializer):
    category_public_id = serializers.SlugRelatedField(
        slug_field="public_id",
        source="category",
        queryset=Category.objects.none(),
        required=False,
        allow_null=True,
    )
    product_public_id = serializers.SlugRelatedField(
        slug_field="public_id",
        source="product",
        queryset=Product.objects.none(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = BulkDiscount
        fields = [
            "public_id",
            "target_type",
            "category_public_id",
            "product_public_id",
            "discount_type",
            "discount_value",
            "priority",
            "start_date",
            "end_date",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        store = self.context.get("store")
        if not store:
            self.fields["category_public_id"].queryset = Category.objects.none()
            self.fields["product_public_id"].queryset = Product.objects.none()
            return
        self.fields["category_public_id"].queryset = Category.objects.filter(store=store)
        self.fields["product_public_id"].queryset = Product.objects.filter(store=store, is_active=True)

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except DjangoValidationError as exc:
            if getattr(exc, "message_dict", None):
                raise serializers.ValidationError(exc.message_dict) from exc
            raise serializers.ValidationError(
                {"non_field_errors": list(getattr(exc, "messages", [str(exc)]))}
            ) from exc

    def update(self, instance, validated_data):
        try:
            return super().update(instance, validated_data)
        except DjangoValidationError as exc:
            if getattr(exc, "message_dict", None):
                raise serializers.ValidationError(exc.message_dict) from exc
            raise serializers.ValidationError(
                {"non_field_errors": list(getattr(exc, "messages", [str(exc)]))}
            ) from exc
