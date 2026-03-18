"""Store service layer."""

from django.conf import settings


def sync_store_owner_to_user(store):
    """
    Sync Store owner_name and owner_email to the owner User's first_name, last_name, and email.

    Finds the User with StoreMembership role=OWNER for this store and updates their profile.
    """
    from .models import StoreMembership

    membership = (
        StoreMembership.objects.filter(
            store=store,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        )
        .select_related("user")
        .first()
    )
    if not membership:
        return

    user = membership.user
    update_fields = []

    if store.owner_name:
        parts = store.owner_name.strip().split(None, 1)
        first = parts[0][:150] if parts else ""
        last = parts[1][:150] if len(parts) > 1 else ""
        if user.first_name != first or user.last_name != last:
            user.first_name = first
            user.last_name = last
            update_fields.extend(["first_name", "last_name"])

    if store.owner_email and "@" in store.owner_email:
        email = store.owner_email.strip()[:254]
        if user.email != email:
            user.email = email
            user.username = email[:150]
            update_fields.extend(["email", "username"])

    if update_fields:
        user.save(update_fields=update_fields)
