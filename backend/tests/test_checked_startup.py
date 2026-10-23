"""Autonomous startup safety checks; opt-in PostgreSQL uses owned synthetic schema."""
import asyncio
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from start_backend import StartupBlocked, check_schema, disabled, migrate
from app.models import Base
from test_inactivity_shadow import database

@pytest.mark.parametrize('values',[{'analytics_signals_enabled':True},{'analytics_automation_enabled':True},{'analytics_signals_mode':'ENABLED'}])
def test_rollback_refuses_enabled(values):
    with pytest.raises(StartupBlocked):disabled(SimpleNamespace(**values))

def test_disabled_legacy_settings_are_supported():
    disabled(SimpleNamespace(analytics_signals_enabled=False,analytics_signals_mode='SHADOW'))

def test_real_schema_revision_drift_and_migration_lock(monkeypatch):
    async def scenario():
        async with database() as (engine,factory):
            async with factory() as db:
                schema=await db.scalar(sa.text('select current_schema()'))
                await db.execute(sa.text('CREATE TABLE alembic_version(version_num varchar(32) NOT NULL)'))
                await db.execute(sa.text("INSERT INTO alembic_version VALUES('20261022_24')"));await db.commit()
            sync=sa.create_engine(engine.url.set(drivername='postgresql+psycopg'),poolclass=sa.pool.NullPool,connect_args={'options':'-c search_path='+schema})
            try:
                statements=[]
                sa.event.listen(sync,'before_cursor_execute',lambda c,cur,sql,p,ctx,m:statements.append(sql))
                await asyncio.to_thread(check_schema,sync,Base.metadata,'20261022_24')
                assert not any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','ALTER','CREATE','DROP')) for sql in statements)
                async with factory() as db:
                    await db.execute(sa.text("UPDATE alembic_version SET version_num='20261022_25'"));await db.commit()
                with pytest.raises(StartupBlocked,match='Unsupported'):
                    await asyncio.to_thread(check_schema,sync,Base.metadata,'20261022_24')
                async with factory() as db:
                    await db.execute(sa.text("UPDATE alembic_version SET version_num='20261022_24'"));await db.execute(sa.text('ALTER TABLE sales ALTER COLUMN total_amount TYPE numeric(19,2)'));await db.commit()
                with pytest.raises(StartupBlocked,match='Column contract'):
                    await asyncio.to_thread(check_schema,sync,Base.metadata,'20261022_24')
                from start_backend import SCHEMA_LOCK
                with sync.connect() as blocker:
                    blocker.execute(sa.text('select pg_advisory_lock(:k)'),{'k':SCHEMA_LOCK});blocker.commit()
                    monkeypatch.setattr('start_backend.subprocess.run',lambda *a,**k:pytest.fail('Locked migration must not run Alembic'))
                    try:
                        with pytest.raises(StartupBlocked,match='Another schema job'):
                            await asyncio.to_thread(migrate,sync,'20261022_24')
                    finally:blocker.execute(sa.text('select pg_advisory_unlock(:k)'),{'k':SCHEMA_LOCK});blocker.commit()
            finally:sync.dispose()
    asyncio.run(scenario())
