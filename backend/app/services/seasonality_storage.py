"""Small reads in reports; no history query or model fitting here."""

from datetime import datetime, timezone
from itertools import islice
from sqlalchemy import select, update
from app.models import SeasonalityProfile, SeasonalityState, ImportBatch, CatalogLookup
from app.services.catalog_attributes import identity as catalog_identity
from app.services.product_analytics_runtime import ensure_headroom
from app.services.seasonality import ALGORITHM_VERSION, identity, recommend


async def state(db, *, lock=False):
    result = (
        await db.scalar(
            select(SeasonalityState).where(SeasonalityState.id == 1).with_for_update()
        )
        if lock
        else await db.get(SeasonalityState, 1)
    )
    if result is None:
        result = SeasonalityState(id=1)
        db.add(result)
        await db.flush()
    return result


async def changed(db, *, rebuild=False):
    await state(db)
    values = {"source_revision": SeasonalityState.source_revision + 1}
    if rebuild:
        values["rebuild_required"] = True
    await db.execute(
        update(SeasonalityState).where(SeasonalityState.id == 1).values(**values)
    )


async def imports_pending(db):
    return bool(
        await db.scalar(
            select(ImportBatch.id)
            .where(ImportBatch.status.in_(["queued", "processing"]))
            .limit(1)
        )
    )


async def attach(
    rows, db, criteria, today, *, comparable_scope=True, analysis_end=None
):
    current = await db.get(SeasonalityState, 1) if db is not None else None
    generation = current.active_generation if current else 0
    valid = bool(
        generation
        and generation == current.generation
        and not current.rebuild_required
        and current.algorithm_version == ALGORITHM_VERSION
        and current.first_year == today.year - 2
        and current.phase in ("idle", "calculate", "cleanup")
        and current.source_revision == current.job_revision
    )
    # Existing report rows can share a code after an article change. Compare
    # their combined observed volume to the same product's historical profile.
    observed = {}
    if comparable_scope:
        for row in rows:
            observed[row["key"]] = observed.get(row["key"], 0) + row.get("units_30", 0)
    explanations = {}
    if valid and await imports_pending(db):
        valid = False
    iterator = iter(rows)
    while batch := list(islice(iterator, 250)):
        ensure_headroom()
        keys = {identity(row["key"]): row["key"] for row in batch}
        profiles = {}
        if valid:
            records = (
                await db.execute(
                    select(
                        SeasonalityProfile.identity,
                        SeasonalityProfile.product_key,
                        SeasonalityProfile.data,
                    ).where(
                        SeasonalityProfile.generation == generation,
                        SeasonalityProfile.identity.in_(keys),
                        SeasonalityProfile.dirty.is_(False),
                        SeasonalityProfile.coverage == current.coverage,
                    )
                )
            ).all()
            profiles = {
                record.identity: record.data
                for record in records
                if record.product_key == keys[record.identity]
                and isinstance(record.data, dict)
                and record.data.get("version") == ALGORITHM_VERSION
                and record.data.get("years") == [today.year - 2, today.year - 1]
            }
        articles = {
            catalog_identity(None, key.split(":", 1)[1]): digest
            for digest, key in keys.items()
            if key.startswith("article:")
        }
        ambiguous = set()
        if db is not None and articles:
            bad = (
                await db.scalars(
                    select(CatalogLookup.identity).where(
                        CatalogLookup.identity.in_(articles),
                        CatalogLookup.status == "ambiguous",
                    )
                )
            ).all()
            ambiguous = {articles[key] for key in bad}
        for row in batch:
            digest = identity(row["key"])
            info = recommend(
                row,
                profiles.get(digest) if digest not in ambiguous else None,
                criteria,
                today,
                comparable_scope=comparable_scope,
                analysis_end=analysis_end,
                observed_units=observed.get(row["key"]) if comparable_scope else None,
            )
            # Reasons contain only finite month/action variants, never product
            # values. Share equal strings across rows to avoid repeated text.
            if digest in ambiguous:
                info["reason"] = (
                    "Артикул неоднозначен в локальном каталоге; сезонная рекомендация не выбирает случайный товар."
                )
            info["reason"] = explanations.setdefault(info["reason"], info["reason"])
            row["seasonality"] = info
    return {
        "as_of": today,
        "years": [today.year - 2, today.year - 1],
        "ready": valid,
        "last_success": current.last_success if current else None,
        "phase": current.phase if current else "not_initialized",
        "scope": "Все магазины; исторические остатки неизвестны",
    }


async def metadata(db, today):
    current = await db.get(SeasonalityState, 1) if db is not None else None
    pending = (
        await imports_pending(db) if current and current.active_generation else False
    )
    return {
        "as_of": today,
        "years": [today.year - 2, today.year - 1],
        "last_success": current.last_success if current else None,
        "phase": current.phase if current else "not_initialized",
        "ready": bool(
            not pending
            and current
            and current.active_generation
            and current.active_generation == current.generation
            and current.algorithm_version == ALGORITHM_VERSION
            and current.first_year == today.year - 2
            and not current.rebuild_required
            and current.source_revision == current.job_revision
            and current.phase in ("idle", "calculate", "cleanup")
        ),
        "scope": "Все магазины; исторические остатки неизвестны",
    }
