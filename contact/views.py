from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response

from meta_pixel.service import meta_conversions

from .models import ContactSubmission
from .serializers import ContactSubmissionSerializer


class ContactCreateView(APIView):
    """Submit contact form."""
    permission_classes = []  # allow unauthenticated
    authentication_classes = []

    def post(self, request):
        ser = ContactSubmissionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        meta_conversions.track_contact(request)
        return Response({'status': 'sent'}, status=status.HTTP_201_CREATED)
