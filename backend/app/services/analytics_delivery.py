from sqlalchemy import select
from app.models import AnalyticsDeliveryHead, AnalyticsDeliveryPublication


async def publish_delivery(db, run):
    """Called under the existing analytics worker leadership lock; caller commits.

    Holding this row lock until commit prevents sequence allocation/commit reorder.
    No audit BIGSERIAL is exposed as a cross-run cursor.
    """
    head = await db.scalar(select(AnalyticsDeliveryHead).where(AnalyticsDeliveryHead.id == 1).with_for_update())
    if head is None:
        head = AnalyticsDeliveryHead(id=1, sequence=0)
        db.add(head)
        await db.flush()
    existing = await db.scalar(select(AnalyticsDeliveryPublication).where(AnalyticsDeliveryPublication.run_id == run.id))
    if existing:
        return existing.sequence
    head.sequence += 1
    db.add(AnalyticsDeliveryPublication(sequence=head.sequence, run_id=run.id))
    await db.flush()
    return head.sequence
