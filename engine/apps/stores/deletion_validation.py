from __future__ import annotations

from django.core.exceptions import ValidationError

from .models import Store


STORE_EMAIL_REQUIRED_FOR_DELETION_MESSAGE = "Add a store email before deleting your store."


def require_store_contact_email_for_deletion(*, store: Store) -> None:
    """
    Backend source of truth: a store must have a contact email before it can be deleted.
    """
    contact = (getattr(store, "contact_email", "") or "").strip()
    if not contact:
        raise ValidationError(STORE_EMAIL_REQUIRED_FOR_DELETION_MESSAGE)

