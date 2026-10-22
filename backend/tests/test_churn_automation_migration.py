"""Round-trip only within an explicitly guarded disposable PostgreSQL schema."""
import asyncio
import importlib.util
from pathlib import Path
from sqlalchemy import text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_inactivity_shadow import database


def test_automation_migration_defaults_and_preserves_shadow():
    async def scenario():
        async with database() as (engine,factory):
            async with engine.begin() as conn:
                names=('analytics_automation_decisions','analytics_automation_events','analytics_automation_releases','analytics_automation_identities','analytics_automation_policy')
                for name in names:await conn.execute(text('DROP TABLE '+name))
                path=Path(__file__).parents[1]/'alembic/versions/20261022_24_churn_automation.py'
                spec=importlib.util.spec_from_file_location('churn_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                def upgrade(c):
                    with Operations.context(MigrationContext.configure(c)):module.upgrade()
                await conn.run_sync(upgrade)
                row=(await conn.execute(text('SELECT enabled,approved,settings_json,sequence FROM analytics_automation_policy'))).one()
                assert row==(False,False,{},0)
                assert await conn.scalar(text("SELECT count(*) FROM pg_indexes WHERE schemaname=current_schema() AND indexname='ix_automation_release_event'"))==1
                def downgrade(c):
                    with Operations.context(MigrationContext.configure(c)):module.downgrade()
                await conn.run_sync(downgrade)
                assert await conn.scalar(text("SELECT to_regclass('analytics_signal_runs')")) is not None
    asyncio.run(scenario())
