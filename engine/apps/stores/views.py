from django.contrib.auth import get_user_model
from rest_framework import viewsets, mixins, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from config.permissions import IsPlatformRequest, IsStoreAdmin, IsStoreStaff
from engine.apps.billing.feature_gate import get_feature_config, get_limit
from engine.core.tenancy import get_active_store

from .models import Store, StoreDeletionJob, StoreMembership, StoreSettings
from .serializers import (
    DeleteStoreRequestSerializer,
    StoreSerializer,
    StoreMembershipSerializer,
    StoreSettingsSerializer,
)

User = get_user_model()


class StoreViewSet(viewsets.ModelViewSet):
    """
    Platform onboarding + store details.

    - On PLATFORM_HOSTS: list/create stores for the authenticated user.
    - On TENANT hosts (or when active store is set): retrieve/update the current store.
    """

    serializer_class = StoreSerializer
    queryset = Store.objects.all()
    # Do NOT expose numeric PKs — use public_id in all URLs
    lookup_field = 'public_id'

    def get_permissions(self):
        if self.action in {"list", "create"}:
            return [permissions.IsAuthenticated(), IsPlatformRequest()]
        return [permissions.IsAuthenticated(), IsStoreAdmin()]

    def get_queryset(self):
        if self.action == "list":
            return Store.objects.filter(
                memberships__user=self.request.user,
                memberships__is_active=True,
            ).distinct()

        ctx = get_active_store(self.request)
        if not ctx.store:
            return Store.objects.none()
        return Store.objects.filter(id=ctx.store.id)

    def create(self, request, *args, **kwargs):
        # Enforce store limit from feature gate
        config = get_feature_config(request.user)
        max_stores = config["limits"].get("max_stores", 0)
        if max_stores == 0 and not config["limits"] and not config["features"]:
            return Response(
                {"detail": "No active subscription. Please contact support to activate a plan."},
                status=status.HTTP_403_FORBIDDEN,
            )
        owned_store_count = Store.objects.filter(
            memberships__user=request.user,
            memberships__role=StoreMembership.Role.OWNER,
            memberships__is_active=True,
        ).distinct().count()
        if owned_store_count >= max_stores:
            return Response(
                {"detail": f"Store limit reached for your plan (max {max_stores})."},
                status=status.HTTP_403_FORBIDDEN,
            )

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

        domain = None
        requested_domain = (request.data.get("domain") or "").strip().lower()
        if requested_domain:
            if Store.objects.filter(domain__iexact=requested_domain).exists():
                return Response({"detail": "domain is already in use."}, status=status.HTTP_400_BAD_REQUEST)
            domain = requested_domain

        store = Store.objects.create(
            name=name,
            domain=domain,
            owner_name=owner_name[:255],
            owner_email=owner_email[:254],
            store_type=store_type_raw,
            currency=(request.data.get("currency") or "BDT").strip()[:8],
            contact_email=(request.data.get("contact_email") or "").strip()[:254],
            phone=(request.data.get("phone") or "").strip()[:50],
            address=(request.data.get("address") or "").strip(),
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

        return Response(StoreSerializer(store).data, status=status.HTTP_201_CREATED)


class StoreMembershipViewSet(viewsets.ModelViewSet):
    """
    Manage memberships for the active store.
    """

    permission_classes = [permissions.IsAuthenticated, IsStoreAdmin]
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
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    View/update settings for the active store.
    """

    permission_classes = [permissions.IsAuthenticated, IsStoreStaff]
    serializer_class = StoreSettingsSerializer

    def get_permissions(self):
        # After store deactivation, `IsStoreStaff` would deny access because the
        # membership is set to `is_active=False`. Deletion endpoints must remain
        # reachable so the frontend can poll progress and complete redirect.
        if self.action in {"delete_store", "delete_status"}:
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsStoreStaff()]

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
        obj = self.get_object()
        if request.method == "GET":
            serializer = self.get_serializer(obj)
            return Response(serializer.data)
        serializer = self.get_serializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="delete")
    def delete_store(self, request):
        """
        Irreversibly delete the active store (irreversible on the DB level),
        but return immediately after enqueueing a Celery job.

        Security: backend performs strict exact-match validation (email + store name)
        and requires OWNER role.
        """

        serializer = DeleteStoreRequestSerializer(data=request.data)
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
        store_name = serializer.validated_data["store_name"]
        if request.user.email != account_email or store.name != store_name:
            return Response(
                {"detail": "Invalid confirmation inputs."},
                status=status.HTTP_403_FORBIDDEN,
            )

        max_stores = get_limit(request.user, "max_stores")
        if max_stores <= 1:
            redirect_route = "/onboarding"
            next_store_public_id = None
        else:
            # Premium: if the user has another active owned store, switch to it.
            other_store = (
                Store.objects.filter(
                    memberships__user=request.user,
                    memberships__role=StoreMembership.Role.OWNER,
                    memberships__is_active=True,
                    is_active=True,
                )
                .exclude(id=store.id)
                .order_by("created_at")
                .first()
            )
            redirect_route = "/" if other_store else "/onboarding"
            next_store_public_id = other_store.public_id if other_store else None

        # Deactivate immediately so the store cannot be accessed while deletion runs.
        store.is_active = False
        store.save(update_fields=["is_active"])
        StoreMembership.objects.filter(user=request.user, store=store, is_active=True).update(
            is_active=False
        )

        # Create a progress-tracked job row.
        job = StoreDeletionJob.objects.create(
            user=request.user,
            store_public_id_snapshot=store.public_id,
            store_id_snapshot=store.id,
            status=StoreDeletionJob.Status.PENDING,
            current_step=StoreDeletionJob.STEP_REMOVING_ORDERS,
            redirect_route=redirect_route,
            next_store_public_id=next_store_public_id,
        )

        # Enqueue irreversible hard delete.
        from .tasks import hard_delete_store

        async_result = hard_delete_store.delay(job.public_id)
        job.celery_task_id = async_result.id
        job.save(update_fields=["celery_task_id"])

        # Re-issue JWT(s) for post-deletion navigation context.
        refresh = RefreshToken.for_user(request.user)
        access = refresh.access_token
        if next_store_public_id:
            refresh["active_store_id"] = next_store_public_id
            access["active_store_id"] = next_store_public_id

        return Response(
            {
                "job_id": job.public_id,
                "access": str(access),
                "refresh": str(refresh),
                "redirect_route": redirect_route,
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

        return Response(
            {
                "status": job.status,
                "current_step": job.current_step,
                "error_message": job.error_message or None,
            },
            status=status.HTTP_200_OK,
        )

