from django.contrib.auth import get_user_model
from django.db import transaction
from django.core.exceptions import ValidationError
from rest_framework import viewsets, mixins, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone

from config.permissions import (
    DenyAPIKeyAccess,
    IsDashboardUser,
    IsStoreAdmin,
    IsStoreStaff,
    IsVerifiedUser,
)
from engine.core.tenancy import get_active_store
from engine.core.tenant_drf import ProvenTenantContextMixin

from .audit import write_store_lifecycle_audit
from .confirmation import confirm_delete_phrase, confirm_remove_phrase, confirm_store_name_against_store
from .lifecycle_emails import (
    queue_restore_otp_emails,
    queue_store_delete_cancelled,
    queue_store_delete_otp_email,
    queue_store_restored_active,
    queue_store_removed_inactive,
    queue_delete_scheduled,
)
from .models import (
    Store,
    StoreApiKey,
    StoreDeletionJob,
    StoreDeletionOtpChallenge,
    StoreLifecycleAuditLog,
    StoreMembership,
    StoreRestoreChallenge,
    StoreSettings,
)
from .serializers import (
    DeleteStoreOtpRequestSerializer,
    DeleteStoreOtpVerifySerializer,
    RecoverableStoreSerializer,
    RemoveStoreRequestSerializer,
    RestoreSendSerializer,
    RestoreVerifySerializer,
    StoreSerializer,
    StoreMembershipSerializer,
    StoreSettingsSerializer,
)
from .store_lifecycle import (
    DELETE_OTP_TTL_MINUTES,
    create_deletion_schedule_otp_challenge,
    create_restore_challenge,
    is_restore_challenge_complete,
    remove_store,
    restore_store_after_otp,
    schedule_permanent_delete,
    verify_deletion_schedule_otp,
    verify_restore_challenge_step,
)
from .services import (
    allocate_unique_store_code,
    create_store_api_key,
    get_active_store_api_key,
    get_cached_store_settings,
    invalidate_store_settings_cache,
    normalize_store_code_base_from_name,
    revoke_store_api_key,
    set_cached_store_settings,
)
from .deletion_validation import (
    STORE_EMAIL_REQUIRED_FOR_DELETION_MESSAGE,
    require_store_contact_email_for_deletion,
)

User = get_user_model()


def _reissue_jwt_after_losing_active_store(request, excluded_store_id: int) -> dict:
    """When the active store becomes non-ACTIVE, redirect to recover or onboarding."""
    user = request.user
    has_recoverable = Store.objects.filter(
        memberships__user=user,
        memberships__role=StoreMembership.Role.OWNER,
        memberships__is_active=True,
        status__in=[Store.Status.INACTIVE, Store.Status.PENDING_DELETE],
    ).exists()
    redirect_route = "/recover" if has_recoverable else "/onboarding"
    next_store_public_id = None
    refresh = RefreshToken.for_user(user)
    access = refresh.access_token
    return {
        "access": str(access),
        "refresh": str(refresh),
        "redirect_route": redirect_route,
        "next_store_public_id": next_store_public_id,
    }


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
    lookup_field = 'public_id'

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [DenyAPIKeyAccess(), IsDashboardUser()]
        if self.action == "create":
            return [DenyAPIKeyAccess(), IsVerifiedUser()]
        if self.action in {"recoverable", "restore_send_codes", "restore_verify"}:
            return [DenyAPIKeyAccess(), IsVerifiedUser()]
        if self.action == "remove":
            return [DenyAPIKeyAccess(), IsStoreAdmin()]
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
        """
        Store deletion is only supported through the OTP-confirmed lifecycle flow
        under /store/settings/delete/*.
        """
        return Response({"detail": "Method \"DELETE\" not allowed."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def create(self, request, *args, **kwargs):
        if getattr(request.user, "owned_store", None) is not None:
            return Response(
                {
                    "detail": (
                        "You already have a store. Please restore or permanently delete it "
                        "before creating a new one."
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

    def _owner_membership_for_public_id(self, request, store_public_id: str):
        return (
            StoreMembership.objects.filter(
                user=request.user,
                store__public_id=store_public_id,
                role=StoreMembership.Role.OWNER,
                is_active=True,
            )
            .select_related("store")
            .first()
        )

    @action(detail=False, methods=["get"], url_path="recoverable")
    def recoverable(self, request):
        qs = (
            Store.objects.filter(
                memberships__user=request.user,
                memberships__role=StoreMembership.Role.OWNER,
                memberships__is_active=True,
                status__in=[Store.Status.INACTIVE, Store.Status.PENDING_DELETE],
            )
            .distinct()
            .order_by("-updated_at", "id")
        )
        return Response(RecoverableStoreSerializer(qs, many=True).data)

    @action(detail=False, methods=["post"], url_path="remove")
    def remove(self, request):
        serializer = RemoveStoreRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ctx = get_active_store(request)
        if not ctx.store or not ctx.membership:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        if ctx.membership.role != StoreMembership.Role.OWNER:
            return Response(
                {"detail": "Only the store owner can remove the store."},
                status=status.HTTP_403_FORBIDDEN,
            )
        d = serializer.validated_data
        if not confirm_store_name_against_store(d["store_name"], ctx.store):
            return Response({"detail": "Invalid confirmation inputs."}, status=status.HTTP_403_FORBIDDEN)
        if not confirm_remove_phrase(d["confirmation_phrase"]):
            return Response({"detail": "Invalid confirmation inputs."}, status=status.HTTP_403_FORBIDDEN)
        with transaction.atomic():
            store = Store.objects.select_for_update().get(pk=ctx.store.pk)
            try:
                remove_store(store=store, user=request.user)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            write_store_lifecycle_audit(
                user=request.user,
                store=store,
                action=StoreLifecycleAuditLog.Action.STORE_REMOVE,
            )
        store.refresh_from_db()
        queue_store_removed_inactive(store)
        tokens = _reissue_jwt_after_losing_active_store(request, store.id)
        return Response(tokens)

    @action(detail=False, methods=["post"], url_path="restore/send-codes")
    def restore_send_codes(self, request):
        ser = RestoreSendSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        store_public_id = ser.validated_data["store_public_id"]
        purpose = ser.validated_data["purpose"]
        m = self._owner_membership_for_public_id(request, store_public_id)
        if not m:
            return Response({"detail": "Store not found."}, status=status.HTTP_404_NOT_FOUND)
        store = m.store
        client_key = str(request.user.pk) if request.user.is_authenticated else (
            request.META.get("REMOTE_ADDR", "") or "unknown"
        )
        try:
            challenge, owner_plain, contact_plain = create_restore_challenge(
                store=store,
                purpose=purpose,
                client_key=client_key,
                owner_email_fallback=request.user.email,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        queue_restore_otp_emails(
            store=store,
            owner_plain=owner_plain or "",
            contact_plain=contact_plain,
            single_channel=challenge.single_channel,
            owner_email_fallback=request.user.email,
        )
        return Response(
            {
                "challenge_public_id": challenge.public_id,
                "single_channel": challenge.single_channel,
                "expires_at": challenge.expires_at.isoformat(),
            }
        )

    @action(detail=False, methods=["post"], url_path="restore/verify")
    def restore_verify(self, request):
        ser = RestoreVerifySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        m = self._owner_membership_for_public_id(request, d["store_public_id"])
        if not m:
            return Response({"detail": "Store not found."}, status=status.HTTP_404_NOT_FOUND)
        store = m.store
        challenge = StoreRestoreChallenge.objects.filter(
            public_id=d["challenge_public_id"],
            store=store,
        ).first()
        if not challenge:
            return Response({"detail": "Invalid challenge."}, status=status.HTTP_400_BAD_REQUEST)
        owner_code = (d.get("owner_code") or "").strip() or None
        contact_code = (d.get("contact_code") or "").strip() or None
        try:
            verify_restore_challenge_step(
                challenge=challenge,
                owner_code=owner_code,
                contact_code=contact_code,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        challenge.refresh_from_db()
        if not is_restore_challenge_complete(challenge):
            return Response({"complete": False, "detail": "Additional verification required."})
        prev_pending = store.status == Store.Status.PENDING_DELETE
        restore_store_after_otp(store=store)
        write_store_lifecycle_audit(
            user=request.user,
            store=store,
            action=StoreLifecycleAuditLog.Action.STORE_RESTORE,
            metadata={"previous_status": "pending_delete" if prev_pending else "inactive"},
        )
        if prev_pending:
            queue_store_delete_cancelled(store)
        else:
            queue_store_restored_active(store)
        StoreRestoreChallenge.objects.filter(pk=challenge.pk).delete()
        tokens = _reissue_jwt_active_store(request, store.public_id)
        return Response({**tokens, "complete": True})


class StoreMembershipViewSet(ProvenTenantContextMixin, viewsets.ModelViewSet):
    """
    Manage memberships for the active store.
    """

    permission_classes = [DenyAPIKeyAccess, IsStoreAdmin]
    serializer_class = StoreMembershipSerializer
    # Do NOT expose numeric PKs — use public_id in all URLs
    lookup_field = 'public_id'

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
        if self.action == "delete_status":
            return [DenyAPIKeyAccess(), IsVerifiedUser()]
        if self.action in {"delete_store", "delete_send_otp", "delete_confirm"}:
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

    @action(detail=False, methods=["post"], url_path="delete")
    def delete_store(self, request):
        """Deprecated: use delete/send-otp and delete/confirm with email OTP."""
        return Response(
            {
                "detail": (
                    "Permanent deletion must be confirmed with an email OTP. "
                    "Use delete/send-otp and delete/confirm."
                ),
                "code": "delete_requires_otp",
            },
            status=status.HTTP_410_GONE,
        )

    @action(detail=False, methods=["post"], url_path="delete/send-otp")
    def delete_send_otp(self, request):
        """Validate confirmation text; send 6-digit OTP to store owner email only."""
        serializer = DeleteStoreOtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ctx = get_active_store(request)
        store = ctx.store
        membership = ctx.membership
        if not store or not membership:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        if membership.role != StoreMembership.Role.OWNER:
            return Response(
                {"detail": "Only the store owner can delete the store."},
                status=status.HTTP_403_FORBIDDEN,
            )

        account_email = serializer.validated_data["account_email"]
        if request.user.email != account_email:
            return Response({"detail": "Invalid confirmation inputs."}, status=status.HTTP_403_FORBIDDEN)
        if not confirm_store_name_against_store(serializer.validated_data["store_name"], store):
            return Response({"detail": "Invalid confirmation inputs."}, status=status.HTTP_403_FORBIDDEN)
        if not confirm_delete_phrase(serializer.validated_data["confirmation_phrase"]):
            return Response({"detail": "Invalid confirmation inputs."}, status=status.HTTP_403_FORBIDDEN)

        if store.status == Store.Status.PENDING_DELETE:
            return Response(
                {"detail": "Permanent deletion is already scheduled for this store."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            require_store_contact_email_for_deletion(store=store)
        except ValidationError:
            return Response(
                {"detail": STORE_EMAIL_REQUIRED_FOR_DELETION_MESSAGE},
                status=status.HTTP_400_BAD_REQUEST,
            )

        client_key = str(request.user.pk) if request.user.is_authenticated else (
            request.META.get("REMOTE_ADDR", "") or "unknown"
        )
        try:
            challenge, plain = create_deletion_schedule_otp_challenge(
                store=store,
                user=request.user,
                client_key=client_key,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        queue_store_delete_otp_email(store=store, code=plain, owner_email_fallback=request.user.email)
        write_store_lifecycle_audit(
            user=request.user,
            store=store,
            action=StoreLifecycleAuditLog.Action.STORE_DELETE_OTP_SENT,
        )
        return Response(
            {
                "challenge_public_id": challenge.public_id,
                "expires_at": challenge.expires_at.isoformat(),
                "otp_ttl_minutes": DELETE_OTP_TTL_MINUTES,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="delete/confirm")
    def delete_confirm(self, request):
        """Verify OTP, then schedule permanent deletion (same job + lifecycle as legacy flow)."""
        serializer = DeleteStoreOtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ctx = get_active_store(request)
        if not ctx.store or not ctx.membership:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        if ctx.membership.role != StoreMembership.Role.OWNER:
            return Response(
                {"detail": "Only the store owner can delete the store."},
                status=status.HTTP_403_FORBIDDEN,
            )

        challenge = StoreDeletionOtpChallenge.objects.filter(
            public_id=serializer.validated_data["challenge_public_id"],
            store=ctx.store,
            user=request.user,
        ).first()
        if not challenge:
            return Response({"detail": "Invalid or expired verification."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            verify_deletion_schedule_otp(
                challenge=challenge,
                code=serializer.validated_data["otp"],
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            store = Store.objects.select_for_update().get(pk=ctx.store.pk)

            try:
                require_store_contact_email_for_deletion(store=store)
            except ValidationError:
                return Response(
                    {"detail": STORE_EMAIL_REQUIRED_FOR_DELETION_MESSAGE},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if store.status == Store.Status.PENDING_DELETE:
                StoreDeletionOtpChallenge.objects.filter(pk=challenge.pk).delete()
                return Response(
                    {"detail": "Permanent deletion is already scheduled for this store."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            existing_job = StoreDeletionJob.objects.filter(
                store_id_snapshot=store.id,
                status=StoreDeletionJob.Status.PENDING,
            ).first()
            if existing_job:
                StoreDeletionOtpChallenge.objects.filter(pk=challenge.pk).delete()
                tokens = _reissue_jwt_after_losing_active_store(request, store.id)
                return Response(
                    {
                        "job_id": existing_job.public_id,
                        "scheduled_delete_at": store.delete_at.isoformat() if store.delete_at else None,
                        **tokens,
                    },
                    status=status.HTTP_200_OK,
                )

            try:
                schedule_permanent_delete(store=store, user=request.user)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            job = StoreDeletionJob.objects.create(
                user=request.user,
                store_public_id_snapshot=store.public_id,
                store_id_snapshot=store.id,
                delete_at_snapshot=store.delete_at,
                lifecycle_version_snapshot=store.lifecycle_version,
                status=StoreDeletionJob.Status.PENDING,
                current_step="Scheduled — permanent deletion pending",
                redirect_route="/onboarding",
                next_store_public_id=None,
            )

            StoreDeletionOtpChallenge.objects.filter(pk=challenge.pk).delete()

            write_store_lifecycle_audit(
                user=request.user,
                store=store,
                action=StoreLifecycleAuditLog.Action.STORE_DELETE_SCHEDULED,
            )

        store.refresh_from_db()
        queue_delete_scheduled(store, from_inactivity=False)
        tokens = _reissue_jwt_after_losing_active_store(request, store.id)

        return Response(
            {
                "job_id": job.public_id,
                "scheduled_delete_at": store.delete_at.isoformat() if store.delete_at else None,
                **tokens,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="delete-status")
    def delete_status(self, request):
        """
        Fetch deletion progress for a job id (user-scoped).
        """

        job_id = request.query_params.get("job_id")
        if not job_id:
            return Response({"detail": "job_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        job = StoreDeletionJob.objects.filter(public_id=job_id, user=request.user).first()
        if not job:
            return Response({"detail": "Job not found."}, status=status.HTTP_404_NOT_FOUND)

        scheduled_delete_at = None
        live = Store.objects.filter(id=job.store_id_snapshot).first()
        if live and live.delete_at:
            scheduled_delete_at = live.delete_at.isoformat()
        elif job.delete_at_snapshot:
            scheduled_delete_at = job.delete_at_snapshot.isoformat()

        return Response(
            {
                "status": job.status,
                "current_step": job.current_step,
                "error_message": job.error_message or None,
                "scheduled_delete_at": scheduled_delete_at,
            },
            status=status.HTTP_200_OK,
        )

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
        StoreApiKey.objects.filter(
            store=ctx.store,
            revoked_at__isnull=True,
            is_active=True,
        ).update(revoked_at=timezone.now(), is_active=False, updated_at=timezone.now())
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

