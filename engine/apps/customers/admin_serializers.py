from rest_framework import serializers

from .models import Customer, CustomerAddress


class AdminCustomerAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerAddress
        fields = [
            "id",
            "label",
            "name",
            "phone",
            "address_line1",
            "address_line2",
            "city",
            "region",
            "postal_code",
            "country",
            "is_default_shipping",
            "is_default_billing",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class AdminCustomerSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source="user.email", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)
    addresses = AdminCustomerAddressSerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id",
            "store",
            "user",
            "user_email",
            "user_username",
            "phone",
            "marketing_opt_in",
            "default_shipping_address",
            "default_billing_address",
            "extra_data",
            "addresses",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AdminCustomerListSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source="user.email", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id",
            "user",
            "user_email",
            "user_username",
            "phone",
            "marketing_opt_in",
            "extra_data",
            "created_at",
        ]
