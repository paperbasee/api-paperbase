"""Central time helpers: UTC storage, Asia/Dhaka display formatting."""

from __future__ import annotations

from datetime import date, datetime, timezone as py_tz
from zoneinfo import ZoneInfo

from django.utils import timezone as dj_tz

BD_TZ = ZoneInfo("Asia/Dhaka")


def now_utc() -> datetime:
    return dj_tz.now()


def to_utc(dt: datetime) -> datetime:
    if dj_tz.is_naive(dt):
        return dj_tz.make_aware(dt, py_tz.utc)
    return dt


def to_bd(dt: datetime) -> datetime:
    return to_utc(dt).astimezone(BD_TZ)


def bd_today() -> date:
    return now_utc().astimezone(BD_TZ).date()


def bd_calendar_date(dt: datetime | None = None) -> date:
    if dt is None:
        return bd_today()
    return to_bd(dt).date()


def format_bd(dt: datetime | None = None) -> str:
    if dt is None:
        dt = now_utc()
    return to_bd(dt).strftime("%d-%m-%Y %H:%M")


def format_bd_with_label(dt: datetime | None = None) -> str:
    return f"{format_bd(dt)} (GMT+6)"


def format_bd_date(dt: datetime | date | None = None) -> str:
    """
    Calendar date as DD-MM-YYYY in Asia/Dhaka for datetimes; plain formatting for date.
    """
    if dt is None:
        d = bd_today()
    elif isinstance(dt, datetime):
        d = bd_calendar_date(dt)
    elif isinstance(dt, date):
        d = dt
    else:
        d = bd_today()
    return d.strftime("%d-%m-%Y")
