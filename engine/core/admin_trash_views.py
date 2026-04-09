from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from config.permissions import DenyAPIKeyAccess, IsPlatformSuperuserOrStoreAdmin
from engine.core.models import TrashItem
from engine.core.tenancy import get_active_store
from engine.core.tenant_drf import ProvenTenantContextMixin
from engine.core.trash_service import permanent_delete_trash_item, restore_trash_item


class AdminTrashItemSerializer(serializers.ModelSerializer):
    entity_name = serializers.SerializerMethodField()

    class Meta:
        model = TrashItem
        fields = [
            "id",
            "entity_type",
            "entity_id",
            "entity_public_id",
            "entity_name",
            "deleted_at",
            "expires_at",
            "is_restored",
        ]
        read_only_fields = [
            "id",
            "entity_type",
            "entity_id",
            "entity_public_id",
            "deleted_at",
            "expires_at",
            "is_restored",
        ]

    @staticmethod
    def get_entity_name(obj: TrashItem) -> str:
        snap = obj.snapshot_json or {}
        if obj.entity_type == TrashItem.EntityType.PRODUCT:
            prod = snap.get("product") or {}
            name = (prod.get("name") or "").strip()
            return name or "—"
        if obj.entity_type == TrashItem.EntityType.ORDER:
            o = snap.get("order") or {}
            shipping = (o.get("shipping_name") or "").strip()
            onum = (o.get("order_number") or "").strip()
            if shipping and onum:
                return f"{shipping} (#{onum})"
            if shipping:
                return shipping
            if onum:
                return f"Order #{onum}"
            return "—"
        return "—"


class AdminTrashViewSet(
    ProvenTenantContextMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Store-scoped trash (internal lifecycle). Lookup is TrashItem integer PK only.
    """

    serializer_class = AdminTrashItemSerializer
    permission_classes = [DenyAPIKeyAccess, IsPlatformSuperuserOrStoreAdmin]

    def get_queryset(self):
        qs = TrashItem.objects.all()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        qs = qs.filter(store=ctx.store).exclude(entity_type=TrashItem.EntityType.ORDER)
        if self.action == "list":
            raw = (self.request.query_params.get("include_restored") or "").lower()
            if raw not in ("1", "true", "yes"):
                qs = qs.filter(is_restored=False)
        return qs.order_by("-deleted_at", "-id")

    def perform_destroy(self, instance):
        ctx = get_active_store(self.request)
        if not ctx.store or instance.store_id != ctx.store.id:
            raise PermissionDenied(detail="You do not have permission to delete this trash item.")
        if instance.entity_type == TrashItem.EntityType.ORDER:
            raise NotFound()
        if instance.is_restored:
            instance.delete()
            return
        permanent_delete_trash_item(trash_item=instance)

    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):
        instance = self.get_object()
        ctx = get_active_store(request)
        if not ctx.store or instance.store_id != ctx.store.id:
            raise PermissionDenied(detail="You do not have permission to restore this item.")
        if instance.entity_type == TrashItem.EntityType.ORDER:
            raise NotFound()
        if instance.is_restored:
            return Response(
                {"detail": "This item was already restored."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            restore_trash_item(trash_item=instance, store=ctx.store)
        except DjangoValidationError as exc:
            detail = getattr(exc, "message_dict", None) or list(getattr(exc, "messages", [str(exc)]))
            return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)
        instance.refresh_from_db()
        return Response(AdminTrashItemSerializer(instance).data, status=status.HTTP_200_OK)
