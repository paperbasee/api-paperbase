from rest_framework import serializers

from .models import ContactSubmission


class AdminContactSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactSubmission
        fields = ['id', 'name', 'phone', 'email', 'message', 'created_at']
        read_only_fields = ['id', 'name', 'phone', 'email', 'message', 'created_at']
