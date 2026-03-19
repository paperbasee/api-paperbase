from rest_framework import serializers
from .models import Customer, CustomerAddress


class CustomerAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerAddress
        fields = [
            'public_id', 'label', 'name', 'phone', 'address_line1', 'address_line2',
            'city', 'region', 'postal_code', 'country',
            'is_default_shipping', 'is_default_billing', 'created_at',
        ]
        read_only_fields = ['public_id', 'created_at']


class CustomerProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    addresses = CustomerAddressSerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = ['user', 'email', 'username', 'phone', 'marketing_opt_in', 'addresses', 'created_at', 'updated_at']
