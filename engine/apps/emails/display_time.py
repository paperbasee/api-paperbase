"""Fixed GMT+6 formatting for transactional email copy (independent of server TIME_ZONE)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from django.utils import timezone as dj_tz

# Standard display zone for all customer-facing email timestamps (GMT+6, no DST).
EMAIL_DISPLAY_TZ = timezone(timedelta(hours=6))


def _ensure_aware_utc(dt: datetime) -> datetime:
    if dj_tz.is_naive(dt):
        return dj_tz.make_aware(dt, dj_tz.utc)
    return dt


def format_email_datetime(dt: datetime | None = None) -> str:
    """Format an instant as YYYY-MM-DD HH:MM:SS AM/PM in GMT+6 (callers/templates add zone label if needed)."""
    if dt is None:
        dt = dj_tz.now()
    return _ensure_aware_utc(dt).astimezone(EMAIL_DISPLAY_TZ).strftime("%Y-%m-%d %I:%M:%S %p")


def format_email_date_in_display_tz(dt: datetime | None = None) -> str:
    """Calendar date in GMT+6 as YYYY-MM-DD (e.g. payment date lines)."""
    if dt is None:
        dt = dj_tz.now()
    return _ensure_aware_utc(dt).astimezone(EMAIL_DISPLAY_TZ).date().isoformat()
