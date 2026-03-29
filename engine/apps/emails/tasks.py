from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from config.celery import app
from engine.core.tenant_execution import system_scope

from engine.apps.orders.order_summary_formatting import build_order_email_context

from .constants import ORDER_CONFIRMED
from .services import send_email


@app.task(name="engine.apps.emails.send_order_email")
def send_order_email_task(order_public_id: str) -> None:
    """
    Send ORDER_CONFIRMED to the customer after courier dispatch (premium + store setting).
    Idempotent via customer_confirmation_sent_at and row lock.
    """
    from engine.apps.emails.triggers import should_send_customer_confirmation_order_email
    from engine.apps.orders.models import Order

    with system_scope(reason="send_order_email_task"):
        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .select_related("store")
                .prefetch_related("items__product", "items__variant")
                .filter(public_id=order_public_id)
                .first()
            )
            if not order:
                return
            if order.customer_confirmation_sent_at is not None:
                return
            if not should_send_customer_confirmation_order_email(order):
                return
            customer_email = (order.email or "").strip()
            if not customer_email:
                return
            store = order.store
            from engine.apps.couriers.models import Courier

            provider_code = (order.courier_provider or "").strip()
            provider_label = dict(Courier.Provider.choices).get(
                provider_code, provider_code.replace("_", " ").title() if provider_code else ""
            )
            consignment = (order.courier_consignment_id or "").strip()
            ctx = {
                "store_name": store.name,
                "order_number": order.order_number,
                "customer_name": (order.shipping_name or "").strip(),
                "total": str(order.total),
                "currency": store.currency,
                "courier_provider": provider_code,
                "courier_provider_label": provider_label,
                "courier_consignment_id": consignment,
            }
            ctx.update(build_order_email_context(order))
            send_email(ORDER_CONFIRMED, customer_email, ctx)
            order.customer_confirmation_sent_at = timezone.now()
            order.save(update_fields=["customer_confirmation_sent_at"])


@app.task(name="engine.apps.emails.send_email")
def send_email_task(
    email_type: str,
    to_email: str,
    context: dict | None = None,
    from_email: str | None = None,
):
    with system_scope(reason="send_email_task"):
        send_email(email_type, to_email, context or {}, from_email=from_email)
