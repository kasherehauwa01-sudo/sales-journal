"""Private source context for the host signer; never reachable from HTTP."""
from datetime import timedelta
from sqlalchemy import select, text

from app.config import settings
from app.models import (AnalyticsAutomationIdentity as Identity, AnalyticsAutomationPolicy as Policy,
                        AnalyticsClientEvaluation, AnalyticsSignalRun, AnalyticsSignalState,
                        ImportBatch, SalesSourceState, ChurnSchedule)
from app.services.analytics_automation import digest, identity_key, source_manifest_summary
from app.services.churn_ftp_trigger import ftp_cycle_ready
from app.services.churn_preliminary import PRELIMINARY, MATCHING_POLICY
from app.services.churn_schedule import guard_job
from app.services.churn_source_trust import TRUSTED_IMPORT, policy_mode, schedule_agrees
from app.services.source_control import SOURCE_LOCK


async def trusted_context(db, job, now):
    row = await db.get(ChurnSchedule, 1)
    guard_job(job, row, now)
    if job.kind != "daily" or job.status not in {"AWAITING_EVIDENCE", "BLOCKED"} or job.release_id:
        raise ValueError("JOB_NOT_READY_FOR_CONTEXT")
    if policy_mode(row.settings_json) != TRUSTED_IMPORT:
        return {"source_trust_mode": "INDEPENDENT"}
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SOURCE_LOCK})
    policy = await db.get(Policy, 1)
    if (not settings.analytics_automation_enabled or not policy or not policy.enabled or not policy.approved
            or policy.revision != row.settings_json["producer_policy_revision"]
            or not schedule_agrees(row.settings_json, policy.settings_json)):
        raise ValueError("TRUSTED_POLICY_NOT_ACTIVE")
    run = await db.get(AnalyticsSignalRun, job.run_id)
    state = await db.get(AnalyticsSignalState, 1)
    revision = await db.scalar(select(SalesSourceState.source_revision).where(SalesSourceState.id == 1))
    pending = await db.scalar(select(ImportBatch.id).where(ImportBatch.status.in_(["queued", "processing"])).limit(1))
    if (not run or run.status != "SUCCEEDED" or run.as_of != job.business_date - timedelta(days=1)
            or not state or state.published_run_id != run.id or revision != run.source_revision
            or pending is not None or not await ftp_cycle_ready(db, run.as_of)):
        raise ValueError("TRUSTED_FTP_SOURCE_NOT_READY")
    conf = policy.settings_json
    if set(conf["required_sources"]) != {"sales_ftp", "clientsvr"}:
        raise ValueError("TRUSTED_SOURCE_NAMES_MISMATCH")
    full = run.metadata_json["scope"]
    scope = {k: v for k, v in full.items() if k != "client_keys"}
    identities = {item.subject_key: item for item in await db.scalars(select(Identity).limit(10001))}
    if len(identities) > 10000:
        raise ValueError("TRUSTED_BINDINGS_LIMIT")
    rows = await db.stream_scalars(select(AnalyticsClientEvaluation).where(
        AnalyticsClientEvaluation.run_id == run.id, AnalyticsClientEvaluation.eligible.is_(True),
        AnalyticsClientEvaluation.status == "ACTIVE", AnalyticsClientEvaluation.signal_type == "CHURN_RISK"
    ).order_by(AnalyticsClientEvaluation.client_key).execution_options(yield_per=500))
    assignments, targets = [], []
    async for candidate in rows:
        subject = candidate.client_key
        binding = identities.get(identity_key(subject, scope))
        item = {"subject_hash": digest(subject)}
        if binding:
            item.update(client_id=binding.client_id, manager_id=binding.manager_id, revision=binding.revision)
            if binding.status in ("VERIFIED", PRELIMINARY) and float(candidate.confidence) >= conf["min_confidence"]:
                signal = candidate.data.get("signal") or {}
                targets.append(dict(item, client_key=subject, status=binding.status,
                                    last_purchase=signal.get("anchor_purchase_date")))
        else:
            item["missing"] = True
        assignments.append(item)
        if len(assignments) > 10000 or len(targets) > 100:
            raise ValueError("TRUSTED_CANDIDATE_LIMIT")
    await rows.close()
    fact_count, facts_hash = await source_manifest_summary(db, run)
    day = run.as_of.isoformat()
    return dict(source_trust_mode=TRUSTED_IMPORT, job_id=job.id, scope=scope, targets=targets,
                max_age=conf["max_manifest_age_seconds"], statement=dict(
                    version=1, issuer=conf["issuer"], source_run_id=run.id,
                    source_revision=run.source_revision, as_of=day, scope_hash=digest(full),
                    public_scope_hash=digest(scope), identities_hash=digest(assignments),
                    complete=False, complete_through=day, declared_complete_through=day,
                    source_trust_mode=TRUSTED_IMPORT, import_trust_approval_id=conf["import_trust_approval_id"],
                    fact_count=fact_count, facts_hash=facts_hash, candidate_count=len(assignments),
                    matching_policy=MATCHING_POLICY))
