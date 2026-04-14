import logging
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers, viewsets, mixins, status

from engine.utils.bd_query import filter_by_bd_date
from engine.utils.time import bd_today
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from config.permissions import DenyAPIKeyAccess, IsPlatformSuperuserOrStoreAdmin
from engine.core.activity import log_activity
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.models import ActivityLog
from engine.core.admin_dashboard_cache import invalidate_notifications_and_dashboard_caches
from engine.core.tenancy import get_active_store
from engine.apps.emails.triggers import (
    queue_customer_order_dispatched_email,
    notify_store_new_order,
    should_send_customer_confirmation_order_email,
)
from engine.apps.products.models import Product
from engine.apps.products.variant_utils import resolve_storefront_variant, unit_price_for_line
from engine.apps.shipping.models import ShippingMethod, ShippingZone

from .courier_dispatch import persist_dispatch, resolve_courier, run_courier_api
from .models import Order
from .order_financials import money, preview_lines_to_accounting
from .services import apply_order_status_change
from .admin_serializers import (
    AdminOrderListSerializer,
    AdminOrderSerializer,
    AdminOrderCreateSerializer,
    AdminOrderUpdateSerializer,
)

logger = logging.getLogger(__name__)

ALLOWED_ORDER_STATUSES = {
    Order.Status.PENDING,
    Order.Status.CONFIRMED,
    Order.Status.CANCELLED,
}

ALLOWED_ORDER_FLAGS = {
    Order.Flag.NO_RESPONSE,
    Order.Flag.CALL_LATER,
    Order.Flag.WRONG_NUMBER,
    Order.Flag.BUSY,
    Order.Flag.HIGH_PRIORITY,
}


class AdminOrderViewSet(
    StoreRolePermissionMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Order.objects.select_related(
        "customer", "user", "shipping_zone", "shipping_method", "shipping_rate"
    ).prefetch_related("items__product", "items__variant").all()
    lookup_field = 'public_id'

    def get_serializer_class(self):
        if self.action == "create":
            return AdminOrderCreateSerializer
        if self.action == 'list':
            return AdminOrderListSerializer
        if self.action in ("update", "partial_update"):
            return AdminOrderUpdateSerializer
        return AdminOrderSerializer

    def get_permissions(self):
        if self.action == "destroy":
            return [DenyAPIKeyAccess(), IsPlatformSuperuserOrStoreAdmin()]
        return super().get_permissions()

    def destroy(self, request, *args, **kwargs):
        return Response(
            {"detail": "Method \"DELETE\" not allowed."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=False, methods=["post"], url_path="pricing-preview")
    def pricing_preview(self, request):
        ctx = get_active_store(request)
        if not ctx.store:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        items = request.data.get("items") or []
        if not isinstance(items, list) or not items:
            return Response({"items": "At least one item is required."}, status=status.HTTP_400_BAD_REQUEST)
        product_public_ids = [str(item.get("product_public_id", "")).strip() for item in items]
        products = {
            p.public_id: p
            for p in Product.objects.filter(
                store=ctx.store,
                public_id__in=product_public_ids,
                is_active=True,
                status=Product.Status.ACTIVE,
            ).select_related("category", "category__parent")
        }
        resolved_lines = []
        for item in items:
            public_id = str(item.get("product_public_id", "")).strip()
            quantity = int(item.get("quantity") or 0)
            product = products.get(public_id)
            if not product or quantity <= 0:
                return Response({"items": "Invalid product_public_id or quantity."}, status=status.HTTP_400_BAD_REQUEST)
            try:
                variant = resolve_storefront_variant(
                    product=product,
                    variant_public_id=item.get("variant_public_id"),
                )
            except serializers.ValidationError as exc:
                return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
            catalog_unit = unit_price_for_line(product, variant)
            raw_unit = item.get("unit_price")
            if raw_unit is not None and raw_unit != "":
                chosen = money(raw_unit)
            else:
                chosen = catalog_unit
            resolved_lines.append(
                {
                    "product": product,
                    "variant": variant,
                    "quantity": quantity,
                    "unit_price": chosen,
                }
            )
        shipping_zone_public_id = (request.data.get("shipping_zone_public_id") or "").strip()
        shipping_method_public_id = (request.data.get("shipping_method_public_id") or "").strip()
        zone = ShippingZone.objects.filter(
            store=ctx.store,
            public_id=shipping_zone_public_id,
            is_active=True,
        ).first()
        method = None
        if shipping_method_public_id:
            method = ShippingMethod.objects.filter(
                store=ctx.store,
                public_id=shipping_method_public_id,
                is_active=True,
            ).first()

        out = preview_lines_to_accounting(
            store=ctx.store,
            resolved_lines=resolved_lines,
            shipping_zone_pk=zone.id if zone else None,
            shipping_method_pk=method.id if method else None,
        )
        body = {k: v for k, v in out.items() if not k.startswith("_")}
        return Response(body, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        """
        Use AdminOrderUpdateSerializer for validation/write, but always respond with AdminOrderSerializer.

        This avoids response serialization errors (items are write-only in update serializer).
        """
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = AdminOrderUpdateSerializer(
            instance,
            data=request.data,
            partial=partial,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance.refresh_from_db()
        return Response(AdminOrderSerializer(instance).data, status=status.HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        qs = qs.filter(store=ctx.store)

        customer_public_id = (
            (self.request.query_params.get("customer") or "").strip()
            or (self.request.query_params.get("customer_public_id") or "").strip()
        )
        if customer_public_id:
            qs = qs.filter(customer__public_id=customer_public_id)

        status_value = (self.request.query_params.get("status") or "").strip().lower()
        if status_value in ALLOWED_ORDER_STATUSES:
            qs = qs.filter(status=status_value)

        flag_value = (self.request.query_params.get("flag") or "").strip().lower()
        if flag_value in ALLOWED_ORDER_FLAGS:
            qs = qs.filter(flag=flag_value)

        date_range = (self.request.query_params.get("date_range") or "").strip().lower()
        if date_range == "today":
            qs = filter_by_bd_date(qs, "created_at", bd_today())
        elif date_range == "last_7_days":
            qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=7))
        elif date_range == "last_30_days":
            qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=30))

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(order_number__icontains=search)
                | Q(public_id__icontains=search)
                | Q(courier_consignment_id__icontains=search)
                | Q(shipping_name__icontains=search)
                | Q(phone__icontains=search)
                | Q(email__icontains=search)
                | Q(customer__name__icontains=search)
            )

        # Explicit stable ordering for paginator (avoids UnorderedObjectListWarning;
        # ties on created_at are broken by primary key).
        return qs.annotate(items_count=Count("items")).order_by("-created_at", "id")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        ctx = get_active_store(self.request)
        context["active_store"] = ctx.store
        return context

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValidationError(
                {
                    "detail": (
                        "No active store resolved. Re-login, switch store, or send the "
                        "X-Store-ID header."
                    )
                }
            )
        instance = serializer.save(store=store)
        notify_store_new_order(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="order",
            entity_id=instance.public_id,
            summary=f"Order created: {instance.order_number}",
        )

    @action(detail=True, methods=["post"], url_path="send-to-courier")
    def send_to_courier(self, request, public_id=None):
        order = self.get_object()
        ctx = get_active_store(request)
        store = ctx.store
        if not store:
            raise ValidationError({"detail": "No active store resolved."})

        if order.status == Order.Status.CANCELLED:
            raise ValidationError({"detail": "Cannot send a cancelled order to a courier."})

        if order.status != Order.Status.CONFIRMED:
            raise ValidationError(
                {
                    "detail": "Set the order status to confirmed before sending to a courier.",
                }
            )

        if order.sent_to_courier:
            raise ValidationError({"detail": "This order has already been sent to a courier."})

        if not order.phone:
            raise ValidationError({"detail": "Order phone number is required for courier dispatch."})
        if not order.shipping_address:
            raise ValidationError({"detail": "Shipping address is required for courier dispatch."})

        if not (order.email or "").strip() and should_send_customer_confirmation_order_email(order):
            raise ValidationError(
                {
                    "detail": (
                        "Customer email is required to send the dispatch notification email for this order."
                    )
                }
            )

        courier = resolve_courier(store=store, order=order)
        result = run_courier_api(order, courier)
        persist_dispatch(order, courier, result.get("consignment_id", ""))
        order.refresh_from_db()

        try:
            queue_customer_order_dispatched_email(order)
        except Exception:
            logger.exception("Failed to queue customer order email for %s", order.public_id)

        log_activity(
            request=request,
            action=ActivityLog.Action.CUSTOM,
            entity_type="order",
            entity_id=order.public_id,
            summary=f"Order {order.order_number} sent to {courier.get_provider_display()}",
            metadata={
                "courier_provider": courier.provider,
                "consignment_id": order.courier_consignment_id,
            },
        )
        return Response(AdminOrderSerializer(order).data)

    @action(detail=True, methods=["patch"], url_path="status")
    def update_status(self, request, public_id=None):
        order = self.get_object()
        next_status = (request.data.get("status") or "").strip().lower()
        note = (request.data.get("note") or "").strip()
        if not next_status:
            raise ValidationError({"status": "This field is required."})
        order = apply_order_status_change(order=order, to_status=next_status)
        order.refresh_from_db()
        log_activity(
            request=request,
            action=ActivityLog.Action.CUSTOM,
            entity_type="order",
            entity_id=order.public_id,
            summary=f"Order {order.order_number} status updated",
            metadata={
                "status": order.status,
                "note": note,
            },
        )
        return Response(
            {"order": AdminOrderSerializer(order).data},
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _format_bulk_error(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            detail = exc.detail
            if isinstance(detail, dict) and "detail" in detail:
                inner = detail["detail"]
                return inner if isinstance(inner, str) else str(inner)
            return str(detail)
        return str(exc)

    @action(detail=False, methods=["post"], url_path="bulk-confirm-send-courier")
    def bulk_confirm_send_courier(self, request):
        ctx = get_active_store(request)
        store = ctx.store
        if not store:
            return Response({"detail": "No active store resolved."}, status=status.HTTP_403_FORBIDDEN)

        raw_ids = request.data.get("order_public_ids")
        if not isinstance(raw_ids, list):
            return Response(
                {"order_public_ids": "Expected a list of order public IDs."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results: list[dict] = []
        ok_count = 0
        failed_count = 0

        for raw in raw_ids:
            pid = str(raw or "").strip()
            if not pid:
                continue
            try:
                order = Order.objects.filter(store=store, public_id=pid).first()
                if not order:
                    raise ValidationError({"detail": "Order not found."})

                if order.status == Order.Status.CANCELLED:
                    raise ValidationError({"detail": "Cannot send a cancelled order to a courier."})
                if order.sent_to_courier:
                    raise ValidationError({"detail": "This order has already been sent to a courier."})
                if not order.phone:
                    raise ValidationError({"detail": "Order phone number is required for courier dispatch."})
                if not order.shipping_address:
                    raise ValidationError({"detail": "Shipping address is required for courier dispatch."})

                order = apply_order_status_change(order=order, to_status=Order.Status.CONFIRMED)
                order.refresh_from_db()

                if not (order.email or "").strip() and should_send_customer_confirmation_order_email(order):
                    raise ValidationError(
                        {
                            "detail": (
                                "Customer email is required to send the dispatch notification email "
                                "for this order."
                            )
                        }
                    )

                courier = resolve_courier(store=store, order=order)
                result = run_courier_api(order, courier)
                persist_dispatch(order, courier, result.get("consignment_id", ""))
                order.refresh_from_db()

                try:
                    queue_customer_order_dispatched_email(order)
                except Exception:
                    logger.exception("Failed to queue customer order email for %s", order.public_id)

                log_activity(
                    request=request,
                    action=ActivityLog.Action.CUSTOM,
                    entity_type="order",
                    entity_id=order.public_id,
                    summary=f"Order {order.order_number} sent to {courier.get_provider_display()} (bulk)",
                    metadata={
                        "courier_provider": courier.provider,
                        "consignment_id": order.courier_consignment_id,
                        "bulk": True,
                    },
                )
                results.append({"public_id": pid, "ok": True, "error": None})
                ok_count += 1
            except Exception as exc:
                results.append(
                    {"public_id": pid, "ok": False, "error": self._format_bulk_error(exc)}
                )
                failed_count += 1

        return Response(
            {
                "results": results,
                "summary": {"ok": ok_count, "failed": failed_count},
            },
            status=status.HTTP_200_OK,
        )

    def perform_destroy(self, instance):
        ctx = get_active_store(self.request)
        user = self.request.user
        public_id = instance.public_id
        order_number = getattr(instance, "order_number", "")
        store_public_id = instance.store.public_id
        if getattr(user, "is_superuser", False):
            from engine.core.trash_service import hard_delete_order_for_admin

            hard_delete_order_for_admin(order=instance)
        else:
            if not ctx.store or instance.store_id != ctx.store.id:
                raise PermissionDenied(
                    detail="You do not have permission to delete this order."
                )
            from engine.core.trash_service import soft_delete_order

            soft_delete_order(order=instance, store=ctx.store, deleted_by=user)
        invalidate_notifications_and_dashboard_caches(store_public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="order",
            entity_id=public_id,
            summary=f"Order deleted: {order_number}" if order_number else "Order deleted",
        )
