"""Read-only, bounded browsing of precomputed all-history seasonal data."""
from app.services.report_execution import FullReportRoute

from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, tuple_, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database import get_db
from app.models import (
    SeasonalityMonth,
    SeasonalityStoreMonth,
    SeasonalityProfile,
    SeasonalityState,
)
from app.services.seasonality import describe, ALGORITHM_VERSION
from app.services.seasonality_storage import metadata
from app.services.product_analytics_runtime import ensure_headroom

router = APIRouter(route_class=FullReportRoute, prefix="/reports/seasonality", tags=["Отчеты"])


async def read_snapshot(db):
    # Each response sees a single published generation, even when a worker
    # commits between the metadata and monthly reads. No global DB settings.
    if db.bind.dialect.name == "postgresql":
        await db.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        await db.execute(text("SET LOCAL statement_timeout = '10s'"))
        await db.execute(text("SET LOCAL lock_timeout = '1s'"))


def today():
    return datetime.now(ZoneInfo(settings.autoload_timezone)).date()


def valid_profile(profile, status, state):
    return bool(
        status["ready"]
        and profile
        and not profile.dirty
        and profile.coverage == state.coverage
        and isinstance(profile.data, dict)
        and profile.data.get("version") == ALGORITHM_VERSION
        and profile.data.get("history_scope") == "all"
    )


@router.get("")
async def products(
    search: Annotated[str, Query(max_length=200)] = "",
    after: Annotated[str | None, Query(pattern="^[a-f0-9]{64}$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    db: AsyncSession = Depends(get_db),
    full: bool = False,
):
    ensure_headroom()
    await read_snapshot(db)
    status = await metadata(db, today())
    state = await db.get(SeasonalityState, 1)
    generation = state.active_generation if state else 0
    fields = (
        "version",
        "history_scope",
        "years",
        "pattern",
        "indices",
        "confidence",
        "start_month",
        "peak_month",
        "end_month",
        "season_months",
        "windows",
        "reason",
    )
    query = select(
        SeasonalityProfile.identity,
        SeasonalityProfile.product_key,
        SeasonalityProfile.dirty,
        SeasonalityProfile.coverage,
        *(SeasonalityProfile.data[key].label(key) for key in fields)
    ).where(SeasonalityProfile.generation == generation)
    if search.strip():
        query = query.where(
            SeasonalityProfile.product_key.contains(
                search.strip().casefold(), autoescape=True
            )
        )
    if after and not full:
        query = query.where(SeasonalityProfile.identity > after)
    rows = (
        await db.execute(query.order_by(SeasonalityProfile.identity) if full else query.order_by(SeasonalityProfile.identity).limit(limit + 1))
    ).all()
    items = []
    for profile in (rows if full else rows[:limit]):
        fresh = bool(
            status["ready"]
            and not profile.dirty
            and profile.coverage == state.coverage
            and profile.version == ALGORITHM_VERSION
            and profile.history_scope == "all"
        )
        data = {key: getattr(profile, key) for key in fields} if fresh else None
        items.append(
            {
                "identity": profile.identity,
                "product_key": profile.product_key,
                "seasonality": describe(data, today()),
                "years": data.get("years", []) if data else [],
            }
        )
    return {
        "items": items,
        "next_cursor": rows[limit - 1].identity if not full and len(rows) > limit else None,
        "metadata": status,
    }


@router.get("/{digest}")
async def history(
    digest: str,
    date_from: date | None = None,
    date_to: date | None = None,
    years: Annotated[list[int] | None, Query(max_length=8)] = None,
    store: Annotated[str | None, Query(max_length=500)] = None,
    after_year: int | None = None,
    after_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    limit: Annotated[int, Query(ge=1, le=120)] = 120,
    db: AsyncSession = Depends(get_db),
    full: bool = False,
):
    import re

    if not re.fullmatch("[a-f0-9]{64}", digest):
        raise HTTPException(422, "Некорректный идентификатор")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "Дата начала не может быть позже даты окончания")
    if (after_year is None) != (after_month is None):
        raise HTTPException(422, "Неполный курсор")
    ensure_headroom()
    await read_snapshot(db)
    status = await metadata(db, today())
    state = await db.get(SeasonalityState, 1)
    generation = state.active_generation if state else 0
    profile = await db.get(SeasonalityProfile, (generation, digest))
    if not profile:
        raise HTTPException(404, "Товар отсутствует в завершённом расчёте")
    model = SeasonalityStoreMonth if store else SeasonalityMonth
    query = select(model).where(
        model.generation == generation, model.identity == digest
    )
    if store:
        query = query.where(model.store == store)
    if date_from:
        query = query.where(
            tuple_(model.year, model.month) >= (date_from.year, date_from.month)
        )
    if date_to:
        query = query.where(
            tuple_(model.year, model.month) <= (date_to.year, date_to.month)
        )
    if years:
        query = query.where(model.year.in_(years))
    if after_year is not None and not full:
        query = query.where(tuple_(model.year, model.month) > (after_year, after_month))
    rows = (
        await db.scalars(query.order_by(model.year, model.month) if full else query.order_by(model.year, model.month).limit(limit + 1))
    ).all()
    page = rows if full else rows[:limit]
    fresh = valid_profile(profile, status, state)
    data = profile.data if fresh else None
    return {
        "identity": digest,
        "product_key": profile.product_key,
        "metadata": status,
        "profile": data,
        "seasonality": describe(data, today()),
        "display_scope": store or "Все магазины",
        "display_note": "Фильтры меняют только отображение месячных агрегатов. Базовая модель рассчитана по всей истории всех магазинов. Произвольные даты округляются до месяцев; текущий месяц может быть неполным.",
        "months": [
            {
                "year": row.year,
                "month": row.month,
                "units": str(row.units),
                "positive_lines": row.positive_lines,
                "incomplete_source": row.incomplete_source,
            }
            for row in page
        ],
        "next_cursor": (
            {"year": page[-1].year, "month": page[-1].month}
            if not full and len(rows) > limit
            else None
        ),
    }
