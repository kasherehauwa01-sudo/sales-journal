"""Explicit phase-1 release, never promotion of stored SHADOW rows.

Caller owns transaction. Authorization needs independently signed upstream facts
and reviewed ClientsVR assignments. A local checksum alone is not evidence of
upstream completeness. No production signer/configuration is supplied.
"""
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import math
from uuid import uuid4, uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo
from sqlalchemy import select, text, func
from app.config import settings
from app.models import (AnalyticsAutomationPolicy as Policy, AnalyticsAutomationIdentity as Identity,
    AnalyticsAutomationRelease as Release, AnalyticsAutomationEvent as Event, AnalyticsAutomationDecision as Decision,
    AnalyticsSignalRun, AnalyticsSignalState, AnalyticsClientEvaluation, AnalyticsPurchaseDay, ImportBatch, SeasonalityState)
from app.services.seasonality_worker import SOURCE_LOCK
from app.services.inactivity import json_value, GENERATOR_VERSION, POLICY as INACTIVITY_POLICY
from app.services.analytics_scope import SCOPE_POLICY
from app.services.analytics_source import stored_sales_contract_valid


def canonical(value):
    return json.dumps(json_value(value),sort_keys=True,ensure_ascii=False,separators=(',',':'))


def digest(value):return sha256(canonical(value).encode()).hexdigest()


def identity_key(key,scope):return digest([key,scope])


def known_text(value):
    return isinstance(value,str) and bool(value.strip()) and value.strip().upper() not in {'UNKNOWN','UNSET','NULL','NONE'}


async def source_manifest_facts(db,run):
    """Canonical diagnostic facts; signed expectation must come from upstream, not here."""
    rows=(await db.execute(select(AnalyticsPurchaseDay).where(AnalyticsPurchaseDay.run_id==run.id)
                          .order_by(AnalyticsPurchaseDay.client_key,AnalyticsPurchaseDay.sale_date))).scalars().all()
    return [dict(client_key=r.client_key,day=r.sale_date,checks=r.checks,positive_checks=r.positive_checks,
                 revenue=str(r.revenue)) for r in rows]


async def source_manifest_summary(db,run):
    # Incremental canonical JSON hash; never hydrate the complete fact set in RAM.
    query=(select(AnalyticsPurchaseDay).where(AnalyticsPurchaseDay.run_id==run.id)
           .order_by(AnalyticsPurchaseDay.client_key,AnalyticsPurchaseDay.sale_date).execution_options(yield_per=1000))
    result=await db.stream_scalars(query)
    hashed=sha256();hashed.update(b'[');count=0
    async for row in result:
        if count:hashed.update(b',')
        hashed.update(canonical(dict(client_key=row.client_key,day=row.sale_date,checks=row.checks,
            positive_checks=row.positive_checks,revenue=str(row.revenue))).encode())
        count+=1
    hashed.update(b']')
    return count,hashed.hexdigest()


async def release_churn(db,run_id,envelope,*,preview=False,now=None):
    now=now or datetime.now(timezone.utc)
    # Shared importer/worker lock: revision and frozen source cannot change mid-release.
    await db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':SOURCE_LOCK})
    policy=await db.get(Policy,1,with_for_update=True)
    run=await db.get(AnalyticsSignalRun,run_id)
    reasons=[]; candidate_decisions=[]
    conf=(policy.settings_json if policy and isinstance(policy.settings_json,dict) else {})
    if not settings.analytics_automation_enabled:reasons.append('PRODUCER_DISABLED')
    if not policy or not policy.enabled or not policy.approved:reasons.append('POLICY_NOT_APPROVED')
    minimum=conf.get('min_confidence');max_age=conf.get('max_manifest_age_seconds')
    required=conf.get('required_sources')
    required_valid=isinstance(required,list) and bool(required) and all(known_text(v) for v in required) and len(set(required))==len(required)
    if not required_valid:reasons.append('REQUIRED_SOURCES_NOT_APPROVED')
    if type(minimum) not in (int,float) or not math.isfinite(minimum) or not 0<=minimum<=1:reasons.append('CONFIDENCE_NOT_APPROVED')
    if type(max_age) is not int or max_age<=0:reasons.append('MANIFEST_AGE_NOT_APPROVED')
    if not run or run.status!='SUCCEEDED':reasons.append('RUN_NOT_SUCCEEDED')
    if run:
        control=await db.get(AnalyticsSignalState,1)
        revision=await db.scalar(select(SeasonalityState.source_revision).where(SeasonalityState.id==1))
        pending=await db.scalar(select(ImportBatch.id).where(ImportBatch.status.in_(['queued','processing'])).limit(1))
        closed=(now.astimezone(ZoneInfo(settings.autoload_timezone)).date())
        from datetime import timedelta
        if run.as_of!=closed-timedelta(days=1):reasons.append('RUN_NOT_LAST_CLOSED_DAY')
        if not control or control.published_run_id!=run.id:reasons.append('RUN_NOT_CURRENT_PUBLICATION')
        if revision!=run.source_revision or pending is not None:reasons.append('SOURCE_CHANGED_OR_PENDING')
        if settings.autoload_timezone!='Europe/Moscow' or run.metadata_json.get('source_decision',{}).get('timezone')!=settings.autoload_timezone:reasons.append('AUTOMATION_TIMEZONE_UNSUPPORTED_OR_CHANGED')
        if not stored_sales_contract_valid(run.metadata_json):reasons.append('SHADOW_SOURCE_CONTRACT_INVALID')
        if (run.metadata_json.get('generator_version')!=GENERATOR_VERSION
            or run.metadata_json.get('parameters')!=INACTIVITY_POLICY):reasons.append('ALGORITHM_CHANGED')
    full_scope=run.metadata_json.get('scope',{}) if run else {}
    public_scope={k:v for k,v in full_scope.items() if k!='client_keys'}
    if full_scope.get('policy')!=SCOPE_POLICY or full_scope.get('department')!='Авиаторов' or full_scope.get('buyer_types')!=['Оптовик','Корпоративный']:
        reasons.append('SCOPE_NOT_APPROVED')
    payload=envelope.get('payload') if isinstance(envelope,dict) else None
    signature=envelope.get('signature') if isinstance(envelope,dict) else None
    key=settings.analytics_automation_manifest_key
    valid_signature=(len(key)>=32 and isinstance(payload,dict) and isinstance(signature,str) and len(signature)==64 and signature.isascii()
        and hmac.compare_digest(hmac.new(key.encode(),canonical(payload).encode(),'sha256').hexdigest(),signature))
    if not valid_signature:reasons.append('UPSTREAM_SIGNATURE_INVALID')
    candidates=[]; assignments=[]; bindings={}; alias_clients=set()
    if run:
        candidates=list((await db.scalars(select(AnalyticsClientEvaluation).where(
            AnalyticsClientEvaluation.run_id==run.id,AnalyticsClientEvaluation.eligible.is_(True),
            AnalyticsClientEvaluation.status=='ACTIVE',AnalyticsClientEvaluation.signal_type=='CHURN_RISK')
            .order_by(AnalyticsClientEvaluation.client_key))).all())
        keys=[identity_key(c.client_key,public_scope) for c in candidates]
        if keys:
            bindings={b.subject_key:b for b in (await db.scalars(select(Identity).where(Identity.subject_key.in_(keys)).order_by(Identity.subject_key).with_for_update())).all()}
        alias_clients=set((await db.scalars(select(Identity.client_id).where(Identity.status=='VERIFIED').group_by(Identity.client_id).having(func.count()>1))).all())
        for candidate in candidates:
            binding=bindings.get(identity_key(candidate.client_key,public_scope))
            assignments.append(dict(subject_hash=digest(candidate.client_key),client_id=binding.client_id,
                manager_id=binding.manager_id,revision=binding.revision) if binding else dict(subject_hash=digest(candidate.client_key),missing=True))
    if valid_signature:
        try:
            issued=datetime.fromisoformat(payload['issued_at']);expires=datetime.fromisoformat(payload['valid_until'])
            if issued.tzinfo is None or expires.tzinfo is None or not issued<=now<expires:raise ValueError()
            if type(max_age) is not int or (expires-issued).total_seconds()>max_age or (now-issued).total_seconds()>max_age:raise ValueError()
        except (KeyError,TypeError,ValueError,OverflowError):reasons.append('UPSTREAM_PROOF_EXPIRED_OR_INVALID')
        expected=dict(version=1,issuer=conf.get('issuer'),source_run_id=run_id,
            source_revision=run.source_revision if run else None,as_of=run.as_of.isoformat() if run else None,
            scope_hash=digest(full_scope),identities_hash=digest(assignments),complete_through=run.as_of.isoformat() if run else None)
        if any(payload.get(k)!=v for k,v in expected.items()) or not known_text(conf.get('issuer')) or type(payload.get('source_revision')) is not int or type(payload.get('version')) is not int:reasons.append('UPSTREAM_PROOF_MISMATCH')
        if payload.get('complete') is not True or payload.get('clients_current') is not True or not known_text(payload.get('clients_revision')):
            reasons.append('COMPLETENESS_OR_CLIENTS_UNCONFIRMED')
        revisions=payload.get('source_revisions')
        if not required_valid or not isinstance(revisions,dict) or set(revisions)!=set(required) or any(not known_text(v) for v in revisions.values()):reasons.append('UPSTREAM_SOURCE_SET_UNCONFIRMED')
        fact_count,facts_hash=await source_manifest_summary(db,run) if run else (0,'')
        if not fact_count or type(payload.get('fact_count')) is not int or payload['fact_count']!=fact_count or payload.get('facts_hash')!=facts_hash:reasons.append('UPSTREAM_FACTS_MISMATCH')
    ready=[]
    for candidate,assignment in zip(candidates,assignments):
        why=[]
        binding=bindings.get(identity_key(candidate.client_key,public_scope))
        if binding and binding.client_id in alias_clients:why.append('CLIENT_ID_MULTIPLE_SUBJECTS_REQUIRES_HISTORY_MERGE')
        if not binding or binding.status!='VERIFIED':why.append('IDENTITY_NOT_VERIFIED')
        elif (not isinstance(binding.evidence,dict) or not known_text(binding.client_id) or binding.manager_id<=0 or binding.revision<1
            or any(binding.evidence.get(k) is not True for k in ('all_sales_reviewed','complete_client_history_reviewed','client_unique','manager_unique','nontechnical','no_name_phone_conflict'))
            or not known_text(binding.evidence.get('reviewer')) or not known_text(binding.evidence.get('reason'))):why.append('IDENTITY_OR_MANAGER_UNCONFIRMED')
        if type(minimum) not in (int,float) or float(candidate.confidence)<minimum:why.append('LOW_OR_UNAPPROVED_CONFIDENCE')
        signal=candidate.data.get('signal') or {}
        if signal.get('signal_type')!='CHURN_RISK' or signal.get('status')!='ACTIVE' or signal.get('as_of')!=(run.as_of.isoformat() if run else None):why.append('SIGNAL_NOT_ACTIVE_CHURN')
        candidate_decisions.append(dict(subject_hash=digest(candidate.client_key),reasons=why))
        if not why:ready.append((candidate,binding,signal))
    # Global proof failure always blocks the entire release. Per-candidate refusal is audited.
    result=dict(status='BLOCKED' if reasons or not ready else 'READY',reasons=list(dict.fromkeys(reasons)),
                candidates=len(candidates),ready=len(ready) if not reasons else 0,decisions=candidate_decisions,
                policy_revision=policy.revision if policy else None,
                policy_settings={k:conf.get(k) for k in ('min_confidence','max_manifest_age_seconds','issuer','required_sources')},
                proof_hash=digest(envelope))
    if not ready and not reasons:result['reasons']=['NO_ELIGIBLE_VERIFIED_CHURN']
    existing=await db.scalar(select(Release).where(Release.source_run_id==run_id)) if run else None
    if existing and result['status']=='READY':
        if existing.proof_hash!=digest(envelope) or existing.metadata_json['policy_revision']!=policy.revision:
            result=dict(result,status='BLOCKED',reasons=['IMMUTABLE_RELEASE_CONFLICT'])
        else:result=dict(result,status='EXISTING',release_id=existing.id)
    if preview:return result
    audit=Decision(id=str(uuid4()),source_run_id=run_id,created_at=now,data=result)
    db.add(audit)
    if result['status'] in {'BLOCKED','EXISTING'}:return result
    policy.sequence+=1
    rid=str(uuid4())
    proof=digest(envelope)
    run_public=dict(id=rid,status='SUCCEEDED',as_of=run.as_of.isoformat(),source_revision=run.source_revision,
        finished_at=now.isoformat(),mode='AUTOMATION_APPROVED',automation_allowed=True,scope=public_scope,
        source_decision=dict(coverage_status='CONFIRMED',business_freshness='CURRENT',accepted_data_through=run.as_of.isoformat(),
            effective_as_of=run.as_of.isoformat(),source_revision=run.source_revision,proof_hash=proof,
            method='INDEPENDENT_SIGNED_UPSTREAM_MANIFEST_V1',valid_until=payload['valid_until'],policy_revision=policy.revision))
    db.add(Release(id=rid,sequence=policy.sequence,source_run_id=run_id,created_at=now,proof_hash=proof,
                   metadata_json=dict(run=run_public,policy_revision=policy.revision,policy_settings=result['policy_settings'],manifest=envelope,
                       binding_guards=[dict(key=b.subject_key,revision=b.revision,client_id=b.client_id,manager_id=b.manager_id) for _,b,_ in ready])))
    await db.flush()
    for candidate,binding,signal in ready:
        metrics={k:signal['metrics'][k] for k in ('last_purchase_date','median_interval','mean_interval','q75','mad','days_without_purchase','overdue_threshold','churn_threshold','deviation_ratio','distinct_purchase_days','history_span_days','reference_revenue','source_period_end') if k in signal['metrics']}
        event=dict(source='sales-journal',signal_id=str(uuid5(NAMESPACE_URL,'churn-approved:'+signal['id'])),revision=signal['revision'],
            event_type='AUTOMATION_APPROVED',status='ACTIVE',family='INACTIVITY',signal_type='CHURN_RISK',
            subject=dict(kind='external_id',version=1,key=binding.client_id),assignment=dict(client_id=binding.client_id,manager_id=binding.manager_id,binding_revision=binding.revision),
            score=signal['score'],confidence=signal['confidence'],last_purchase=signal['anchor_purchase_date'],
            analytical_eligible=True,generator_version=GENERATOR_VERSION,as_of=signal['as_of'],calculated_at=now.isoformat(),
            metrics=metrics,mode='AUTOMATION_APPROVED',automation_allowed=True,run=run_public,
            provenance=dict(shadow_signal_id=signal['id'],shadow_run_id=run_id,proof_hash=proof))
        db.add(Event(release_id=rid,payload=json_value(event)))
    await db.flush()
    audit.data=dict(result,status='RELEASED',release_id=rid)
    return audit.data


async def manage_policy(db,action,payload,*,actor,expected_revision):
    """Operator CLI only. Draft revokes approval; no arbitrary business defaults."""
    if not known_text(actor):raise ValueError('Reviewed actor required')
    await db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':SOURCE_LOCK})
    policy=await db.get(Policy,1,with_for_update=True)
    if not policy:
        if expected_revision!=0:raise ValueError('Policy revision conflict')
        policy=Policy(id=1,enabled=False,approved=False,revision=0,sequence=0,settings_json={});db.add(policy)
    if policy.revision!=expected_revision:raise ValueError('Policy revision conflict')
    if action=='draft':
        if not isinstance(payload,dict) or set(payload)-{'min_confidence','max_manifest_age_seconds','issuer','required_sources'}:raise ValueError('Unknown policy fields')
        policy.settings_json=payload;policy.enabled=False;policy.approved=False
    elif action=='approve':
        c=policy.settings_json
        if payload!={'confirm_business_approval':True}:raise ValueError('Explicit business approval required')
        minimum=c.get('min_confidence');sources=c.get('required_sources')
        if (type(minimum) not in (int,float) or not math.isfinite(minimum) or not 0<=minimum<=1
            or type(c.get('max_manifest_age_seconds')) is not int or c['max_manifest_age_seconds']<=0
            or not known_text(c.get('issuer')) or not isinstance(sources,list) or not sources
            or any(not known_text(v) for v in sources) or len(set(sources))!=len(sources)):raise ValueError('Incomplete policy cannot be approved')
        policy.approved=True;policy.enabled=True
    elif action=='revoke':policy.approved=False;policy.enabled=False
    else:raise ValueError('Unknown policy action')
    policy.revision+=1
    db.add(Decision(id=str(uuid4()),source_run_id='POLICY',created_at=datetime.now(timezone.utc),
        data=dict(action=action,actor=actor,policy_revision=policy.revision,settings=policy.settings_json)))
    await db.flush()
    return dict(policy_revision=policy.revision,enabled=policy.enabled,approved=policy.approved)


async def review_identity(db,payload,*,actor,expected_revision):
    if not known_text(actor):raise ValueError('Reviewed actor required')
    await db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':SOURCE_LOCK})
    scope=payload['scope'];client_key=payload['client_key'];status=payload['status']
    if ('client_keys' in scope or scope.get('policy')!=SCOPE_POLICY or scope.get('department')!='Авиаторов'
        or not known_text(client_key) or status not in {'VERIFIED','REVIEW','AMBIGUOUS'}):raise ValueError('Invalid identity scope/status')
    evidence={**payload['evidence'],'reviewer':actor}
    if status=='VERIFIED' and (not known_text(payload.get('client_id')) or type(payload.get('manager_id')) is not int or payload['manager_id']<=0
        or any(evidence.get(k) is not True for k in ('all_sales_reviewed','complete_client_history_reviewed','client_unique','manager_unique','nontechnical','no_name_phone_conflict'))
        or not known_text(evidence.get('reason'))):raise ValueError('Explicit unambiguous reviewed identity/manager required')
    key=identity_key(client_key,scope)
    row=await db.get(Identity,key,with_for_update=True)
    if (row.revision if row else 0)!=expected_revision:raise ValueError('Identity revision conflict')
    if row is None:row=Identity(subject_key=key);db.add(row)
    row.client_id=payload.get('client_id','');row.manager_id=payload.get('manager_id',0)
    row.revision=expected_revision+1;row.status=status;row.evidence=evidence
    db.add(Decision(id=str(uuid4()),source_run_id='IDENTITY',created_at=datetime.now(timezone.utc),
        data=dict(action='IDENTITY_REVIEW',actor=actor,subject_hash=key,binding_revision=row.revision,status=status,evidence=evidence)))
    await db.flush()
    return dict(subject_hash=key,binding_revision=row.revision,status=status)
