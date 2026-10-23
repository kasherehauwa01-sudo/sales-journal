from sqlalchemy import select
from app.models import AnalyticsDeliveryHead, AnalyticsDeliveryPublication
from app.services.analytics_source import stored_sales_contract_valid
from app.services.churn_pilot import algorithm
from app.services.client_identity import ANALYTICAL_NORMALIZATION_VERSION


async def publish_delivery(db, run):
    """Called under the existing analytics worker leadership lock; caller commits.

    Holding this row lock until commit prevents sequence allocation/commit reorder.
    Local PostgreSQL bookkeeping only: no HTTP client, queue, Calltrack write or
    outbound delivery is invoked. Export remains a separately authenticated pull
    API; publication is not permission to send or automate.
    No audit BIGSERIAL is exposed as a cross-run cursor.
    """
    metadata = run.metadata_json
    GENERATOR_VERSION, PARAMETERS_VERSION, POLICY = algorithm(metadata.get("scope") or {})
    if (run.status != "SUCCEEDED" or not stored_sales_contract_valid(metadata)
            or metadata.get("generator_version") != GENERATOR_VERSION
            or metadata.get("parameters_version") != PARAMETERS_VERSION
            or metadata.get("parameters") != POLICY
            or metadata.get("normalization_version") != ANALYTICAL_NORMALIZATION_VERSION
            or metadata["source_decision"].get("shadow_allowed") is not True
            or metadata["source_decision"].get("effective_as_of") != run.as_of.isoformat()):
        raise ValueError("Only completed compatible STORED_SALES SHADOW runs can publish")
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
