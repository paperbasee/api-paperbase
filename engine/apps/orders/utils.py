"""Order number generation utilities."""

from django.db import transaction

from engine.apps.stores.models import Store

from .models import OrderNumberCounter


def get_next_order_number(store: Store) -> str:
    """
    Atomically get next sequential order number.
    8 digits (00000001) up to 99999999, then 9, 10, etc.
    """
    if store is None or not getattr(store, "pk", None):
        raise ValueError("store is required to generate an order number")

    with transaction.atomic():
        # OrderNumberCounter primary key is the Store (OneToOne primary_key=True).
        counter, _ = OrderNumberCounter.objects.select_for_update().get_or_create(
            store=store, defaults={"next_value": 1}
        )
        value = counter.next_value
        counter.next_value += 1
        counter.save(update_fields=["next_value"])

    # Format: 8 digits until 99999999, then 9, 10, ...
    return str(value).zfill(max(8, len(str(value))))
