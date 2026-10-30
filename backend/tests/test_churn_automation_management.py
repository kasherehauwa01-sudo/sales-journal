import asyncio
import pytest
from app.services.churn_pilot import ALGORITHM_POLICY
from app.models import AnalyticsAutomationPolicy as Policy, AnalyticsAutomationIdentity as Identity
from app.services.analytics_automation import manage_policy, review_identity
from test_inactivity_shadow import database


def test_operator_policy_and_identity_are_explicit_versioned_and_fail_closed():
    async def scenario():
        async with database() as (engine,factory):
            async with factory() as db:
                draft=await manage_policy(db,'draft',{},actor='synthetic-operator',expected_revision=0)
                assert draft==dict(policy_revision=1,enabled=False,approved=False)
                await db.commit()
            async with factory() as db:
                with pytest.raises(ValueError):await manage_policy(db,'approve',{'confirm_business_approval':True},actor='synthetic-operator',expected_revision=1)
                await db.rollback()
            async with factory() as db:
                draft=await manage_policy(db,'draft',dict(algorithm_policy=ALGORITHM_POLICY,min_confidence=.5,max_manifest_age_seconds=600,issuer='fixture',required_sources=['fixture-1c']),actor='synthetic-operator',expected_revision=1)
                await db.commit()
            for value in (1, 'true', None):
                async with factory() as db:
                    with pytest.raises(ValueError):
                        await manage_policy(db,'approve',{'confirm_business_approval':value},actor='synthetic-operator',expected_revision=2)
                    await db.rollback()
            for revision in (True, 2.0, '2', -1, None):
                async with factory() as db:
                    with pytest.raises(ValueError):
                        await manage_policy(db,'revoke',{},actor='synthetic-operator',expected_revision=revision)
                    await db.rollback()
            async with factory() as db:
                approved=await manage_policy(db,'approve',{'confirm_business_approval':True},actor='synthetic-operator',expected_revision=2)
                assert approved['approved'];await db.commit()
            async with factory() as db:
                with pytest.raises(ValueError):await manage_policy(db,'revoke',{},actor='synthetic-operator',expected_revision=2)
                await db.rollback()
            async with factory() as db:
                revoked=await manage_policy(db,'revoke',{},actor='synthetic-operator',expected_revision=3)
                assert not revoked['enabled'] and not revoked['approved'];await db.commit()
            request=dict(client_key='fixture',scope=dict(policy='aviatorov-wholesale-corporate/2',department='Авиаторов'),client_id='stable-id',manager_id=3,status='VERIFIED',evidence={'reason':'synthetic'})
            async with factory() as db:
                with pytest.raises(ValueError):await review_identity(db,request,actor='synthetic-reviewer',expected_revision=0)
                await db.rollback()
            for revision in (True, 0.0, '0', -1, None):
                async with factory() as db:
                    with pytest.raises(ValueError):
                        await review_identity(db,request,actor='synthetic-reviewer',expected_revision=revision)
                    await db.rollback()
            request['evidence'].update({k:True for k in ('all_sales_reviewed','complete_client_history_reviewed','client_unique','manager_unique','nontechnical','no_name_phone_conflict')})
            async with factory() as db:
                reviewed=await review_identity(db,request,actor='synthetic-reviewer',expected_revision=0)
                assert reviewed['binding_revision']==1;await db.commit()
            async with factory() as db:
                with pytest.raises(ValueError):await review_identity(db,request,actor='synthetic-reviewer',expected_revision=0)
                await db.rollback()
    asyncio.run(scenario())
