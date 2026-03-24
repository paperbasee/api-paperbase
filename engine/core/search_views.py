from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsDashboardUser
from engine.core.search_serializers import UnifiedSearchResponseSerializer
from engine.core.search_services import search as search_entities
from engine.core.tenancy import get_active_store


class UnifiedSearchView(APIView):
    permission_classes = [IsAuthenticated, IsDashboardUser]

    def get(self, request):
        query = (request.query_params.get("query") or "").strip()
        if not query:
            serializer = UnifiedSearchResponseSerializer(
                {"products": [], "orders": [], "customers": [], "tickets": []}
            )
            return Response(serializer.data)

        ctx = get_active_store(request)
        if not ctx.store:
            serializer = UnifiedSearchResponseSerializer(
                {"products": [], "orders": [], "customers": [], "tickets": []}
            )
            return Response(serializer.data)

        data = search_entities(query=query, store=ctx.store, per_type_limit=10)
        serializer = UnifiedSearchResponseSerializer(data)
        return Response(serializer.data)
