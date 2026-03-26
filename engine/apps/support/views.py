from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from engine.apps.analytics.service import meta_conversions
from engine.core.tenancy import require_api_key_store

from .models import SupportTicket
from .serializers import SupportTicketCreateSerializer, SupportTicketPublicResponseSerializer


class SupportTicketCreateView(APIView):
    """Submit support ticket (guest allowed). Tenant is resolved by API key."""
    permission_classes = []  # allow unauthenticated
    authentication_classes = []
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        store = require_api_key_store(request)

        ser = SupportTicketCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ticket: SupportTicket = ser.save(store=store)
        meta_conversions.track_support_ticket_submission(request)
        return Response(
            SupportTicketPublicResponseSerializer(ticket).data,
            status=status.HTTP_201_CREATED,
        )
