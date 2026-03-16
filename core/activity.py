from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AnonymousUser

from .models import ActivityLog


def log_activity(
    *,
    request,
    action: str,
    entity_type: str,
    entity_id: str | int | None = None,
    summary: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    actor = getattr(request, "user", None)
    if isinstance(actor, AnonymousUser):
        actor = None

    ActivityLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else "",
        summary=summary[:255],
        metadata=metadata or {},
    )

