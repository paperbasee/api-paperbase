from rest_framework import serializers

from .models import SupportTicket, SupportTicketAttachment


class AdminSupportTicketAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportTicketAttachment
        fields = ["id", "file", "created_at"]
        read_only_fields = ["id", "file", "created_at"]


class AdminSupportTicketSerializer(serializers.ModelSerializer):
    attachments = AdminSupportTicketAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = SupportTicket
        fields = [
            "public_id",
            "store_id",
            "name",
            "email",
            "phone",
            "subject",
            "message",
            "order_number",
            "category",
            "priority",
            "status",
            "internal_notes",
            "attachments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "store_id", "created_at", "updated_at", "attachments"]
