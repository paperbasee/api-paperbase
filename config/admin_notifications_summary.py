from datetime import datetime

from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import DenyAPIKeyAccess, IsDashboardUser
from engine.apps.orders.models import Order
from engine.apps.support.models import SupportTicket
from engine.core.admin_notifications_cache import (
    NOTIFICATIONS_SUMMARY_CACHE_TTL,
    notifications_summary_cache_key,
)
from engine.core.request_context import get_dashboard_store_from_request

# Cap notification payloads (not full list PAGE_SIZE).
RECENT_NOTIFICATION_LIMIT = 8
MERGED_NOTIFICATION_ITEMS_MAX = 8


def _dt_iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _sort_ts(value) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if timezone.is_aware(value):
        return value
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return datetime.min.replace(tzinfo=timezone.utc)


def _normalize_order_row(row: dict) -> dict:
    return {
        "public_id": row["public_id"],
        "order_number": row["order_number"],
        "shipping_name": (row.get("shipping_name") or "") or "",
        "created_at": _dt_iso(row.get("created_at")),
        "status": row["status"],
    }


def _normalize_ticket_row(row: dict) -> dict:
    return {
        "public_id": row["public_id"],
        "name": row["name"],
        "phone": (row.get("phone") or "") or "",
        "email": row["email"],
        "created_at": _dt_iso(row.get("created_at")),
        "status": row["status"],
    }


def _rows_from_orm(store):
    recent_order_rows = list(
        Order.objects.filter(store=store)
        .order_by("-created_at")
        .values(
            "public_id", "order_number", "shipping_name", "created_at", "status"
        )[:RECENT_NOTIFICATION_LIMIT]
    )
    recent_ticket_rows = list(
        SupportTicket.objects.filter(store=store)
        .order_by("-created_at")
        .values("public_id", "name", "phone", "email", "created_at", "status")[
            :RECENT_NOTIFICATION_LIMIT
        ]
    )
    return recent_order_rows, recent_ticket_rows


def _counts_from_raw_sql(store):
    order_tbl = Order._meta.db_table
    ticket_tbl = SupportTicket._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
              (SELECT COUNT(*) FROM {order_tbl} WHERE store_id = %s AND status = %s),
              (SELECT COUNT(*) FROM {ticket_tbl}
               WHERE store_id = %s AND status IN (%s, %s))
            """,
            [
                store.pk,
                Order.Status.PENDING,
                store.pk,
                SupportTicket.Status.NEW,
                SupportTicket.Status.IN_PROGRESS,
            ],
        )
        row = cursor.fetchone()
    new_orders_count = int(row[0]) if row and row[0] is not None else 0
    pending_tickets_count = int(row[1]) if row and row[1] is not None else 0
    return new_orders_count, pending_tickets_count


def _build_payload(
    new_orders_count,
    pending_tickets_count,
    unread_count,
    recent_order_rows,
    recent_ticket_rows,
):
    recent_orders = [_normalize_order_row(dict(r)) for r in recent_order_rows]
    recent_tickets = [_normalize_ticket_row(dict(r)) for r in recent_ticket_rows]

    merge_candidates = []
    for row in recent_order_rows:
        r = dict(row)
        shipping = (r.get("shipping_name") or "") or ""
        created = r.get("created_at")
        merge_candidates.append(
            (
                _sort_ts(created),
                {
                    "id": f"order-{r['public_id']}",
                    "type": "new_order",
                    "title": "New order placed",
                    "message": f"Order #{r['order_number']} from {shipping}",
                    "timestamp": _dt_iso(created) or "",
                    "read": False,
                },
            )
        )
    for row in recent_ticket_rows:
        r = dict(row)
        phone = (r.get("phone") or "") or ""
        email = r.get("email") or ""
        contact = phone or email
        created = r.get("created_at")
        merge_candidates.append(
            (
                _sort_ts(created),
                {
                    "id": f"support-ticket-{r['public_id']}",
                    "type": "support_ticket",
                    "title": "New support ticket",
                    "message": f"{r['name']} ({contact})",
                    "timestamp": _dt_iso(created) or "",
                    "read": False,
                },
            )
        )
    merge_candidates.sort(key=lambda x: x[0], reverse=True)
    items = [entry for _, entry in merge_candidates[:MERGED_NOTIFICATION_ITEMS_MAX]]

    return {
        "new_orders_count": new_orders_count,
        "pending_tickets_count": pending_tickets_count,
        "recent_orders": recent_orders,
        "recent_tickets": recent_tickets,
        "items": items,
        "unread_count": unread_count,
    }


def build_notifications_summary_payload(store):
    new_orders_count, pending_tickets_count = _counts_from_raw_sql(store)
    recent_order_rows, recent_ticket_rows = _rows_from_orm(store)
    unread_count = new_orders_count + pending_tickets_count
    return _build_payload(
        new_orders_count,
        pending_tickets_count,
        unread_count,
        recent_order_rows,
        recent_ticket_rows,
    )


class AdminNotificationsSummaryView(APIView):
    """
    Lightweight dashboard notification payload for the active store.
    Scoped by tenant context / get_active_store(request); does not replace list endpoints.
    """

    permission_classes = [DenyAPIKeyAccess, IsDashboardUser]

    def get(self, request):
        store = get_dashboard_store_from_request(request)
        if not store:
            raise PermissionDenied("No active store resolved.")

        cache_key = notifications_summary_cache_key(store.public_id)
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        payload = build_notifications_summary_payload(store)
        cache.set(cache_key, payload, NOTIFICATIONS_SUMMARY_CACHE_TTL)
        return Response(payload)


__all__ = [
    "AdminNotificationsSummaryView",
    "MERGED_NOTIFICATION_ITEMS_MAX",
    "RECENT_NOTIFICATION_LIMIT",
    "build_notifications_summary_payload",
]
