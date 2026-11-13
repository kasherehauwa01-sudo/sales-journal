"""Invalidate RFM and risk snapshots after sales changes, without scheduling analysis."""

from sqlalchemy import select, update
from app.models import SalesSourceState


async def state(db, *, lock=False):
    result = (
        await db.scalar(
            select(SalesSourceState).where(SalesSourceState.id == 1).with_for_update()
        )
        if lock
        else await db.get(SalesSourceState, 1)
    )
    if result is None:
        result = SalesSourceState(id=1)
        db.add(result)
        await db.flush()
    return result


async def changed(db):
    await state(db)
    await db.execute(
        update(SalesSourceState)
        .where(SalesSourceState.id == 1)
        .values(source_revision=SalesSourceState.source_revision + 1)
    )
