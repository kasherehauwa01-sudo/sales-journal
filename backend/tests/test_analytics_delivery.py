"""Authenticated delivery tests use disposable schema and existing real engine."""
import asyncio
from datetime import timedelta
from pathlib import Path
import json
import os
import httpx
from fastapi import FastAPI
from sqlalchemy import select, func
from app.models import AnalyticsDeliveryHead, AnalyticsDeliveryPublication, AnalyticsSignalRun
from app.routers.analytics_delivery import router
from app.routers.calltrack_integration import settings
from app.database import get_db
from app.services.analytics_delivery import publish_delivery
from test_inactivity_shadow import database, seed, finish, ANCHOR


def test_postgres_publication_auth_replay_bounded_frozen_run(monkeypatch):
    async def scope():
        return dict(department=None, client_keys=None, buyer_types=[], policy='test/all')
    monkeypatch.setattr('app.services.analytics_signal_worker.load_analytics_scope', scope)
    monkeypatch.setattr(settings, 'calltrack_integration_token', 'synthetic-test-token')

    async def run():
        async with database() as (engine, factory):
            await seed(factory, clients=3, phones=True)
            result = await finish(factory, engine, ANCHOR + timedelta(days=24))
            rid = result['run_id']
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(AnalyticsDeliveryPublication)) == 1
                assert await publish_delivery(db, await db.get(AnalyticsSignalRun, rid)) == 1
                await db.commit()
            app = FastAPI()
            app.include_router(router, prefix='/api')
            async def session():
                async with factory() as db:
                    yield db
            app.dependency_overrides[get_db] = session
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                assert (await client.get('/api/integrations/calltrack/analytics/publications')).status_code == 401
                client.headers['Authorization'] = 'Bearer synthetic-test-token'
                pubs = (await client.get('/api/integrations/calltrack/analytics/publications?page_size=1')).json()
                assert pubs['items'] == [dict(sequence=1, run_id=rid)]
                latest = (await client.get('/api/integrations/calltrack/analytics/latest')).json()
                assert latest['run']['id'] == rid and latest['automation_allowed'] is False
                url = f'/api/integrations/calltrack/analytics/runs/{rid}/changes'
                first = (await client.get(url, params=dict(page_size=1))).json()
                assert len(first['items']) == 1 and first['next_after'] is not None
                event = first['items'][0]
                assert event['automation_allowed'] is False and event['mode'] == 'SHADOW'
                assert 'known_phones' not in event and 'known_phones' not in event['metrics']
                assert 'client_keys' not in event['run']['scope'] and 'evidence' not in event['run']['source_decision']
                assert event['analytical_eligible'] and event['generator_version'] == 'inactivity/1'
                assert (await client.get(url, params=dict(page_size=101))).status_code == 422
                assert (await client.get(url, params=dict(after=-1))).status_code == 422
                cursor = first['next_after']; all_items = first['items'][:]
                while cursor is not None:
                    page = (await client.get(url, params=dict(page_size=1, after=cursor))).json()
                    all_items += page['items']; cursor = page['next_after']
                assert len(all_items) == 3 and len({e['signal_id'] for e in all_items}) == 3
                await finish(factory, engine, ANCHOR + timedelta(days=25))
                assert (await client.get(url, params=dict(page_size=1))).json() == first
                assert (await client.get('/api/integrations/calltrack/analytics/runs/missing/changes')).status_code == 404
                if target := os.environ.get('ANALYTICS_DELIVERY_FIXTURE'):
                    assert Path(target).is_relative_to('/workspace/scratch')
                    Path(target).write_text(json.dumps(event))
                async with factory() as db:
                    head = await db.get(AnalyticsDeliveryHead, 1)
                    assert head.sequence == 2
                    head.sequence = 99
                    await db.flush()
                    await db.rollback()
                async with factory() as db:
                    assert (await db.get(AnalyticsDeliveryHead, 1)).sequence == 2
    asyncio.run(run())


def test_postgres_delivery_migration_bootstrap_and_downgrade(monkeypatch):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    async def scope():
        return dict(department=None, client_keys=None, buyer_types=[], policy='test/all')
    monkeypatch.setattr('app.services.analytics_signal_worker.load_analytics_scope', scope)
    migration = Path(__file__).parents[1] / 'alembic/versions/20261020_23_analytics_delivery.py'
    spec = importlib.util.spec_from_file_location('delivery_migration_test', migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async def run():
        async with database() as (engine, factory):
            await seed(factory)
            result = await finish(factory, engine, ANCHOR + timedelta(days=24))
            async with engine.begin() as connection:
                for sql in ('DROP TABLE analytics_delivery_publications', 'DROP TABLE analytics_delivery_head',
                            'DROP INDEX ix_analytics_change_run_id'):
                    await connection.execute(text(sql))
                def upgrade(conn):
                    with Operations.context(MigrationContext.configure(conn)):
                        module.upgrade()
                await connection.run_sync(upgrade)
                assert (await connection.execute(text('SELECT sequence,run_id FROM analytics_delivery_publications'))).one() == (1, result['run_id'])
                assert await connection.scalar(text('SELECT sequence FROM analytics_delivery_head WHERE id=1')) == 1
                def downgrade(conn):
                    with Operations.context(MigrationContext.configure(conn)):
                        module.downgrade()
                await connection.run_sync(downgrade)
                assert await connection.scalar(text("SELECT to_regclass('analytics_delivery_publications')")) is None
                assert await connection.scalar(text('SELECT count(*) FROM analytics_signals')) == 1
    asyncio.run(run())
