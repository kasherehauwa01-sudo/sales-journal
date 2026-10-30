"""Shared admission recheck for HTTP pull and private permanent delivery."""
from fastapi import HTTPException
from sqlalchemy import select, func
from app.services.churn_pilot import ALGORITHM_POLICY
from app.models import AnalyticsAutomationRelease as Release


async def automation_available(db):
    from app.config import settings
    from app.models import AnalyticsAutomationPolicy
    policy=await db.get(AnalyticsAutomationPolicy,1)
    if (not settings.analytics_automation_enabled or not policy or not policy.enabled or not policy.approved
            or policy.settings_json.get('algorithm_policy')!=ALGORITHM_POLICY):
        raise HTTPException(503,'Automation release disabled')
    return policy


async def current_release(db, run_id):
    # Keep exactly the same release/identity/source checks as the existing pull API.
    policy=await automation_available(db)
    release=await db.get(Release,run_id)
    if not release:raise HTTPException(404,'Release not found')
    if release.metadata_json.get('algorithm_policy')!=ALGORITHM_POLICY:raise HTTPException(410,'Release algorithm policy incompatible')
    if release.metadata_json['policy_revision']!=policy.revision:raise HTTPException(410,'Release policy revoked')
    permanent = release.metadata_json.get('permanent_policy')
    if permanent:
        from app.models import ChurnSchedule
        schedule = await db.get(ChurnSchedule, 1)
        if (not schedule or not schedule.enabled or not schedule.approved
                or schedule.revision != permanent.get('revision')):
            raise HTTPException(410,'Permanent schedule revoked')
    from datetime import datetime, timezone, timedelta
    from zoneinfo import ZoneInfo
    from app.config import settings
    from app.models import SeasonalityState, ImportBatch, AnalyticsAutomationIdentity
    evidence=release.metadata_json['run']['source_decision']
    now=datetime.now(timezone.utc)
    revision=await db.scalar(select(SeasonalityState.source_revision).where(SeasonalityState.id==1))
    pending=await db.scalar(select(ImportBatch.id).where(ImportBatch.status.in_(['queued','processing'])).limit(1))
    if (revision!=evidence['source_revision'] or pending is not None
        or datetime.fromisoformat(evidence['valid_until'])<=now
        or release.metadata_json['run']['as_of']!=(now.astimezone(ZoneInfo(settings.autoload_timezone)).date()-timedelta(days=1)).isoformat()):
        raise HTTPException(410,'Release source evidence no longer current')
    guards=release.metadata_json.get('binding_guards',[])
    aliases=await db.scalar(select(AnalyticsAutomationIdentity.client_id).where(
        AnalyticsAutomationIdentity.status=='VERIFIED',AnalyticsAutomationIdentity.client_id.in_([g['client_id'] for g in guards])
    ).group_by(AnalyticsAutomationIdentity.client_id).having(func.count()>1).limit(1)) if guards else None
    if aliases is not None:raise HTTPException(410,'Release identity aliases require history merge')
    bindings={b.subject_key:b for b in (await db.scalars(select(AnalyticsAutomationIdentity).where(AnalyticsAutomationIdentity.subject_key.in_([g['key'] for g in guards])))).all()} if guards else {}
    for guard in guards:
        binding=bindings.get(guard['key'])
        if not binding or binding.status!='VERIFIED' or binding.revision!=guard['revision'] or binding.client_id!=guard['client_id'] or binding.manager_id!=guard['manager_id']:
            raise HTTPException(410,'Release identity revoked')
    return release
