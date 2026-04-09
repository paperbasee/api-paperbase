from engine.core.serializers import SafeModelSerializer
from .models import Customer


class CustomerSerializer(SafeModelSerializer):
    class Meta:
        model = Customer
        fields = ['public_id', 'name', 'phone', 'email', 'address', 'total_orders']
        read_only_fields = ['public_id', 'total_orders']
