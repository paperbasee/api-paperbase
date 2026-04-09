"""ORM helpers: Bangladesh calendar date bucketing and filters (Asia/Dhaka)."""

from __future__ import annotations

from datetime import date

from django.db.models import QuerySet
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek

from engine.utils.time import BD_TZ


def trunc_created_bd(field_name: str, bucket: str):
    bucket = (bucket or "day").lower()
    if bucket == "week":
        return TruncWeek(field_name, tzinfo=BD_TZ)
    if bucket == "month":
        return TruncMonth(field_name, tzinfo=BD_TZ)
    return TruncDate(field_name, tzinfo=BD_TZ)


def filter_by_bd_date_range(
    qs: QuerySet,
    field_name: str,
    start: date,
    end: date,
) -> QuerySet:
    alias = f"_bd_{field_name.replace('.', '_')}"
    return qs.annotate(**{alias: TruncDate(field_name, tzinfo=BD_TZ)}).filter(
        **{f"{alias}__gte": start, f"{alias}__lte": end}
    )


def filter_by_bd_date(qs: QuerySet, field_name: str, d: date) -> QuerySet:
    alias = f"_bd_{field_name.replace('.', '_')}"
    return qs.annotate(**{alias: TruncDate(field_name, tzinfo=BD_TZ)}).filter(
        **{alias: d}
    )


def apply_bd_date_filters(
    qs: QuerySet,
    field_name: str,
    *,
    start: date | None = None,
    end: date | None = None,
) -> QuerySet:
    """Filter by calendar date in Asia/Dhaka for ``field_name`` (optional start and/or end)."""
    if start is None and end is None:
        return qs
    alias = f"_bd_{field_name.replace('.', '_')}"
    qs = qs.annotate(**{alias: TruncDate(field_name, tzinfo=BD_TZ)})
    if start is not None:
        qs = qs.filter(**{f"{alias}__gte": start})
    if end is not None:
        qs = qs.filter(**{f"{alias}__lte": end})
    return qs
