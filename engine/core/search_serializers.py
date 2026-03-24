from rest_framework import serializers


class SearchItemSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    title = serializers.CharField()
    subtitle = serializers.CharField(required=False, allow_blank=True)


class UnifiedSearchResponseSerializer(serializers.Serializer):
    products = SearchItemSerializer(many=True)
    orders = SearchItemSerializer(many=True)
    customers = SearchItemSerializer(many=True)
    tickets = SearchItemSerializer(many=True)
