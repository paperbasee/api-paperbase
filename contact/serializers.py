from rest_framework import serializers

from .models import ContactSubmission


class ContactSubmissionSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=False, allow_blank=True, default='')

    class Meta:
        model = ContactSubmission
        fields = ['name', 'phone', 'email', 'message']
