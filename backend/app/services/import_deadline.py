"""Operator-approved 1C schedule, all boundaries in Moscow civil time."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")
EXPORT_TIME = time(23, 40)
DEADLINE_TIME = time(0, 20)


def deadline(day):
    return datetime.combine(day + timedelta(days=1), DEADLINE_TIME, MOSCOW)


def cycle_sales_day(at):
    local = at.astimezone(MOSCOW)
    return local.date() - timedelta(days=int(local.time() < EXPORT_TIME))


def due_sales_day(at):
    local = at.astimezone(MOSCOW)
    return local.date() - timedelta(days=1 if local.time() >= DEADLINE_TIME else 2)
