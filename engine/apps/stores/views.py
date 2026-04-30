from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import viewsets, mixins, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from config.permissions import (
    DenyAPIKeyAccess,
    IsDashboardUser,
    IsStoreAdmin,
    IsStoreStaff,
    IsVerifiedUser,
)
from engine.core.tenancy import get_active_store
from engine.core.tenant_drf import ProvenTenantContextMixin

from .models import (
    Store,
    StoreApiKey,
    StoreMembership,
    StoreSettings,
)
from .serializers import (
    StoreSerializer,
    StoreMembershipSerializer,
    StoreSettingsSerializer,
)
from .services import (
    allocate_unique_store_code,
    create_store_api_key,
    get_active_store_api_key,
    get_cached_store_settings,
    invalidate_store_api_key_resolution_cache_from_digest,
    invalidate_store_settings_cache,
    normalize_store_code_base_from_name,
    revoke_store_api_key,
    set_cached_store_settings,
)

User = get_user_model()


def _deactivate_active_store_api_keys(store) -> None:
    """Rotate active API keys and invalidate resolver cache digests."""
    rows = list(
        StoreApiKey.objects.filter(
            store=store,
            revoked_at__isnull=True,
            is_active=True,
        ).only("id", "key_hash")
    )
    if not rows:
        return
    now = timezone.now()
    StoreApiKey.objects.filter(id__in=[row.id for row in rows]).update(
        revoked_at=now,
        is_active=False,
        updated_at=now,
    )
    for row in rows:
        invalidate_store_api_key_resolution_cache_from_digest(row.key_hash)


def _reissue_jwt_active_store(request, store_public_id: str) -> dict:
    refresh = RefreshToken.for_user(request.user)
    access = refresh.access_token
    refresh["active_store_public_id"] = store_public_id
    access["active_store_public_id"] = store_public_id
    return {
        "access": str(access),
        "refresh": str(refresh),
        "redirect_route": "/",
    }


class StoreViewSet(ProvenTenantContextMixin, viewsets.ModelViewSet):
    """
    Platform onboarding + store details.

    - GET list URL: current store (singular); POST: create if none.
    - retrieve/update by public_id for the active store.
    """

    serializer_class = StoreSerializer
    queryset = Store.objects.all()
    # Do NOT expose numeric PKs — use public_id in all URLs
    lookup_field = "public_id"

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [DenyAPIKeyAccess(), IsDashboardUser()]
        if self.action == "create":
            return [DenyAPIKeyAccess(), IsVerifiedUser()]
        return [DenyAPIKeyAccess(), IsStoreAdmin()]

    def get_queryset(self):
        ctx = get_active_store(self.request)
        if not ctx.store:
            return Store.objects.none()
        return Store.objects.filter(id=ctx.store.id)

    def list(self, request, *args, **kwargs):
        """GET /store/ — current store for the authenticated context."""
        ctx = get_active_store(request)
        if not ctx.store:
            return Response({"detail": "No store."}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(ctx.store)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        """Store deletion is not available via the dashboard API; use Django admin."""
        return Response({"detail": 'Method "DELETE" not allowed.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def create(self, request, *args, **kwargs):
        if getattr(request.user, "owned_store", None) is not None:
            return Response(
                {
                    "detail": (
                        "You already have a store. Contact support if you need help "
                        "with an existing store."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Store creation is allowed before plan selection; dashboard/features stay limited
        # via get_feature_config until subscription is active (storefront APIs gated separately).

        name = (request.data.get("name") or "").strip()
        if not name:
            return Response({"detail": "name is required."}, status=status.HTTP_400_BAD_REQUEST)

        owner_first_name = (request.data.get("owner_first_name") or "").strip()
        owner_last_name = (request.data.get("owner_last_name") or "").strip()
        owner_name_raw = (request.data.get("owner_name") or "").strip()
        if owner_first_name and owner_last_name:
            owner_name = f"{owner_first_name} {owner_last_name}".strip()[:255]
        elif owner_name_raw:
            parts = owner_name_raw.split(None, 1)
            owner_first_name = parts[0][:150] if parts else ""
            owner_last_name = parts[1][:150] if len(parts) > 1 else ""
            owner_name = owner_name_raw[:255]
        else:
            return Response(
                {"detail": "owner_first_name and owner_last_name (or owner_name) are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        owner_email = (request.data.get("owner_email") or "").strip()
        if not owner_email:
            return Response({"detail": "owner_email is required."}, status=status.HTTP_400_BAD_REQUEST)

        store_type_raw = (request.data.get("store_type") or "").strip()[:60]
        if store_type_raw and len(store_type_raw.split()) > 4:
            return Response(
                {"detail": "store_type must be at most 4 words."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        code_base = normalize_store_code_base_from_name(name)
        if not code_base:
            return Response(
                {
                    "detail": (
                        "Could not derive a store code from name; use a name with "
                        "letters or numbers."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        store_code = allocate_unique_store_code(code_base)

        store = Store.objects.create(
            owner=request.user,
            name=name,
            code=store_code,
            owner_name=owner_name[:255],
            owner_email=owner_email[:254],
            store_type=store_type_raw,
            currency=(request.data.get("currency") or "BDT").strip()[:8],
            contact_email=(request.data.get("contact_email") or "").strip()[:254],
            phone=(request.data.get("phone") or "").strip()[:50],
            address=(request.data.get("address") or "").strip(),
            last_activity_at=timezone.now(),
        )
        settings_obj, _ = StoreSettings.objects.get_or_create(store=store)
        modules = request.data.get("modules_enabled") or {}
        if isinstance(modules, dict):
            settings_obj.modules_enabled = {k: bool(v) for k, v in modules.items()}
            settings_obj.save()
        StoreMembership.objects.create(
            user=request.user,
            store=store,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        )

        # Update User's first_name and last_name for auth/profile
        request.user.first_name = owner_first_name[:150]
        request.user.last_name = owner_last_name[:150]
        request.user.save(update_fields=["first_name", "last_name"])

        payload = StoreSerializer(store).data
        tokens = _reissue_jwt_active_store(request, store.public_id)
        return Response({**payload, **tokens}, status=status.HTTP_201_CREATED)


class StoreMembershipViewSet(ProvenTenantContextMixin, viewsets.ModelViewSet):
    """
    Manage memberships for the active store.
    """

    permission_classes = [DenyAPIKeyAccess, IsStoreAdmin]
    serializer_class = StoreMembershipSerializer
    # Do NOT expose numeric PKs — use public_id in all URLs
    lookup_field = "public_id"

    def get_queryset(self):
        ctx = get_active_store(self.request)
        if not ctx.store:
            return StoreMembership.objects.none()
        return StoreMembership.objects.select_related("user", "store").filter(store=ctx.store)

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        serializer.save(store=ctx.store)


class StoreSettingsViewSet(
    ProvenTenantContextMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    View/update settings for the active store.
    """

    permission_classes = [DenyAPIKeyAccess, IsStoreStaff]
    serializer_class = StoreSettingsSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        req = self.request
        store_ctx = get_active_store(req)
        ctx["store"] = store_ctx.store if store_ctx else None
        ctx["membership"] = store_ctx.membership if store_ctx else None
        return ctx

    def get_permissions(self):
        if self.action == "api_key":
            return [DenyAPIKeyAccess(), IsStoreAdmin()]
        return [DenyAPIKeyAccess(), IsStoreStaff()]

    def get_object(self):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise permissions.PermissionDenied("No active store.")
        settings_obj, _ = StoreSettings.objects.get_or_create(store=store)
        return settings_obj

    @action(detail=False, methods=["get", "patch"])
    def current(self, request):
        """GET/PATCH store settings for the active store (no pk required)."""
        ctx = get_active_store(request)
        if request.method == "GET":
            if ctx.store:
                cached = get_cached_store_settings(ctx.store.public_id)
                if cached is not None:
                    return Response(cached)
            obj = self.get_object()
            serializer = self.get_serializer(obj)
            data = serializer.data
            if ctx.store:
                set_cached_store_settings(ctx.store.public_id, data)
            return Response(data)
        obj = self.get_object()
        serializer = self.get_serializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if ctx.store:
            invalidate_store_settings_cache(ctx.store.public_id)
        return Response(serializer.data)

    @action(detail=False, methods=["get", "post"], url_path="api-key")
    def api_key(self, request):
        """
        GET: key metadata (never plaintext).
        POST: rotate key and return plaintext once.
        """
        ctx = get_active_store(request)
        if not ctx.store:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)

        if request.method == "GET":
            row = get_active_store_api_key(ctx.store)
            if row is None:
                return Response({"has_api_key": False}, status=status.HTTP_200_OK)
            return Response(
                {
                    "has_api_key": True,
                    "public_id": row.public_id,
                    "key_prefix": row.key_prefix,
                    "key_type": row.key_type,
                    "name": row.label,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                },
                status=status.HTTP_200_OK,
            )

        name = (request.data.get("name") or "").strip()
        _deactivate_active_store_api_keys(ctx.store)
        key_type = (request.data.get("key_type") or StoreApiKey.KeyType.PUBLIC).strip().lower()
        row, raw_api_key = create_store_api_key(ctx.store, name=name, key_type=key_type)
        return Response(
            {
                "public_id": row.public_id,
                "key_prefix": row.key_prefix,
                "name": row.label,
                "key_type": row.key_type,
                "api_key": raw_api_key,
            },
            status=status.HTTP_201_CREATED,
        )


class StoreAPIKeyManagementViewSet(ProvenTenantContextMixin, viewsets.ViewSet):
    """
    Settings > Network API key management.
    """

    permission_classes = [DenyAPIKeyAccess, IsStoreAdmin]
    lookup_field = "public_id"

    def _active_store(self, request):
        return get_active_store(request).store

    def list(self, request):
        store = self._active_store(request)
        if not store:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        rows = list(
            StoreApiKey.objects.filter(store=store)
            .order_by("-created_at")
            .values("public_id", "label", "key_prefix", "key_type", "created_at", "revoked_at")
        )
        payload = [
            {
                "public_id": r["public_id"],
                "name": r["label"],
                "key_prefix": r["key_prefix"],
                "key_type": r["key_type"],
                "created_at": r["created_at"],
                "revoked_at": r["revoked_at"],
            }
            for r in rows
        ]
        return Response(payload, status=status.HTTP_200_OK)

    def create(self, request):
        store = self._active_store(request)
        if not store:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        name = (request.data.get("name") or "").strip()
        key_type = (request.data.get("key_type") or StoreApiKey.KeyType.PUBLIC).strip().lower()
        # Dashboard UX expects a single current key; creating a new key rotates the old one.
        _deactivate_active_store_api_keys(store)
        row, raw_api_key = create_store_api_key(store, name=name, key_type=key_type)
        return Response(
            {
                "public_id": row.public_id,
                "name": row.label,
                "key_prefix": row.key_prefix,
                "key_type": row.key_type,
                "created_at": row.created_at,
                "api_key": raw_api_key,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="regenerate")
    def regenerate(self, request, public_id=None):
        store = self._active_store(request)
        if not store:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        row = StoreApiKey.objects.filter(
            store=store,
            public_id=public_id,
        ).first()
        if row is None:
            return Response({"detail": "API key not found."}, status=status.HTTP_404_NOT_FOUND)
        revoke_store_api_key(row)
        name = (request.data.get("name") or row.label or "").strip()
        key_type = (request.data.get("key_type") or row.key_type).strip().lower()
        new_row, raw_api_key = create_store_api_key(store, name=name, key_type=key_type)
        return Response(
            {
                "public_id": new_row.public_id,
                "name": new_row.label,
                "key_prefix": new_row.key_prefix,
                "key_type": new_row.key_type,
                "created_at": new_row.created_at,
                "api_key": raw_api_key,
            },
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, public_id=None):
        store = self._active_store(request)
        if not store:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        row = StoreApiKey.objects.filter(
            store=store,
            public_id=public_id,
        ).first()
        if row is None:
            return Response({"detail": "API key not found."}, status=status.HTTP_404_NOT_FOUND)
        revoke_store_api_key(row)
        return Response(status=status.HTTP_204_NO_CONTENT)
