from rest_framework import serializers

from .models import SupportTicket, SupportTicketAttachment


class SupportTicketCreateSerializer(serializers.ModelSerializer):
    attachments = serializers.ListField(
        child=serializers.FileField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )

    class Meta:
        model = SupportTicket
        fields = [
            "name",
            "email",
            "phone",
            "subject",
            "message",
            "order_number",
            "category",
            "priority",
            "attachments",
        ]

    def create(self, validated_data):
        attachments = validated_data.pop("attachments", [])
        ticket = super().create(validated_data)
        for f in attachments:
            SupportTicketAttachment.objects.create(ticket=ticket, file=f)
        return ticket


class SupportTicketPublicResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportTicket
        fields = ["public_id", "created_at", "status"]
