"""Checked startup and real 24→25 migration on explicitly opted-in synthetic PG.

Never use the application's DATABASE_URL as a test source. Native tests require
SCHEMA25_TEST_DATABASE_URL pointing to localhost/analytics_shadow_test.
"""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa

import start_backend as launcher
from app.config import settings
from app.models import Base

BACKEND = Path(__file__).parents[1]
CONTROL_TABLES=set(launcher.IMPORT_CONTROL_CONTRACT) | set(launcher.IMPORT_HISTORY_CONTRACT)
NEW_TABLES = set(launcher.CHURN_COLUMNS)

def metadata25():
    metadata=sa.MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in CONTROL_TABLES:table.to_metadata(metadata)
    return metadata

LEGACY_METADATA=metadata25()


def test_revision_graph_has_one_reviewed_successor():
    config = Config()  # keep Alembic from reconfiguring pytest/global logging
    config.set_main_option('script_location', str(BACKEND/'alembic'))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ['20261114_29']
    assert scripts.get_revision('20261114_29').down_revision=='20261112_28'
    assert scripts.get_revision('20261112_28').down_revision=='20261111_27'
    assert scripts.get_revision('20261030_25').down_revision == '20261022_24'
    assert launcher.DEFAULT_REVISION == '20261114_29'
    assert launcher.MIGRATION_SOURCES['20261114_29']=={'20261112_28','20261114_29'}
    assert launcher.MIGRATION_SOURCES['20261112_28']=={'20261111_27','20261112_28'}
    assert launcher.MIGRATION_SOURCES['20261030_25'] == {'20261022_24', '20261030_25'}
    assert '20261030_25' not in launcher.MIGRATION_SOURCES['20261022_24']


def test_frozen_contract_covers_every_new_model_column_and_key():
    from sqlalchemy.dialects.postgresql import dialect
    for name, columns in launcher.CHURN_COLUMNS.items():
        table = Base.metadata.tables[name]
        assert set(columns) == set(table.c.keys())
        for column in table.columns:
            assert columns[column.name] == (launcher.type_name(column.type, dialect()), column.nullable)
        assert list(table.primary_key.columns.keys()) == ['id']
        if name in launcher.CHURN_UNIQUE:
            unique_name, keys = launcher.CHURN_UNIQUE[name]
            assert any(isinstance(c, sa.UniqueConstraint) and c.name == unique_name
                       and list(c.columns.keys()) == keys for c in table.constraints)
        fks = list(table.foreign_key_constraints)
        assert len(fks) == int(name in launcher.CHURN_FOREIGN_KEYS)


@pytest.mark.parametrize('args,exec_expected', [([], True), (['--check-only'], False), (['--rollback'], True)])
def test_default_launch_checks26_then_execs_without_migration(monkeypatch, args, exec_expected):
    events = []
    engine = SimpleNamespace(dispose=lambda: events.append('dispose'))
    monkeypatch.setattr(launcher, 'sync_engine', lambda config: engine)
    monkeypatch.setattr(launcher, 'check_schema', lambda e,m,r: events.append(('check', r)))
    monkeypatch.setattr(launcher, 'migrate', lambda *a: pytest.fail('Normal startup must not migrate'))
    monkeypatch.setattr(launcher.subprocess, 'run', lambda *a,**k: pytest.fail('No subprocess during checked start'))
    monkeypatch.setattr(launcher.os, 'execvp', lambda executable, argv: events.append(('exec', executable, argv)))
    monkeypatch.setattr(settings, 'analytics_signals_enabled', False)
    monkeypatch.setattr(settings, 'analytics_automation_enabled', False)
    launcher.main(args)
    assert events[:2] == [('check', '20261114_29'), 'dispose']
    assert (len(events) == 3) == exec_expected
    if exec_expected:
        assert events[-1][1] == 'uvicorn'
        assert ('--lifespan' in events[-1][2]) == ('--rollback' in args)


def test_bad_preflight_cannot_start_backend_or_migrate(monkeypatch):
    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(launcher, 'sync_engine', lambda config: engine)
    def blocked(*args): raise launcher.StartupBlocked('incompatible fixture')
    monkeypatch.setattr(launcher, 'check_schema', blocked)
    monkeypatch.setattr(launcher.os, 'execvp', lambda *a: pytest.fail('Must not exec'))
    monkeypatch.setattr(launcher, 'migrate', lambda *a: pytest.fail('Must not migrate'))
    with pytest.raises(launcher.StartupBlocked): launcher.main([])


@pytest.mark.parametrize('target', ['head', 'base', '20261020_23', '20261101_26'])
def test_unreviewed_targets_rejected_before_connect(target):
    with pytest.raises(launcher.StartupBlocked): launcher.migrate(None, target)


def test_migrate_mode_explicitly_checks_disabled_flags_and_never_execs(monkeypatch):
    calls = []
    engine = SimpleNamespace(dispose=lambda: calls.append('dispose'))
    monkeypatch.setattr(launcher, 'sync_engine', lambda config: engine)
    monkeypatch.setattr(launcher, 'migrate', lambda e,r,m: calls.append((r, m)))
    monkeypatch.setattr(launcher.os, 'execvp', lambda *a: pytest.fail('Migration must not start the app'))
    monkeypatch.setattr(settings, 'analytics_signals_enabled', False)
    monkeypatch.setattr(settings, 'analytics_automation_enabled', True)
    with pytest.raises(launcher.StartupBlocked): launcher.main(['--migrate'])
    assert calls == []
    monkeypatch.setattr(settings, 'analytics_automation_enabled', False)
    launcher.main(['--migrate'])
    assert calls == [('20261114_29', Base.metadata), 'dispose']
    with pytest.raises(launcher.StartupBlocked): launcher.main(['--migrate', '--rollback'])


@pytest.fixture
def pg24(monkeypatch):
    url = os.environ.get('SCHEMA25_TEST_DATABASE_URL')
    if not url: pytest.skip('SCHEMA25_TEST_DATABASE_URL required for isolated PostgreSQL')
    parsed = sa.engine.make_url(url)
    assert parsed.host in {'127.0.0.1', 'localhost'} and parsed.database == 'analytics_shadow_test'
    assert parsed.drivername in {'postgresql+psycopg', 'postgresql+asyncpg'}
    sync_url = parsed.set(drivername='postgresql+psycopg')
    admin = sa.create_engine(sync_url, poolclass=sa.pool.NullPool)
    schema = 'schema25_'+uuid4().hex
    with admin.begin() as conn: conn.execute(sa.text('CREATE SCHEMA '+schema))
    engine = sa.create_engine(sync_url, poolclass=sa.pool.NullPool,
                             connect_args={'options': '-csearch_path='+schema})
    # Both the parent check and Alembic subprocess target this owned synthetic schema.
    monkeypatch.setenv('DATABASE_URL', parsed.render_as_string(hide_password=False))
    monkeypatch.setenv('PGOPTIONS', '-csearch_path='+schema)
    monkeypatch.setattr(settings, 'database_url', parsed.render_as_string(hide_password=False))
    monkeypatch.setattr(settings, 'analytics_signals_enabled', False)
    monkeypatch.setattr(settings, 'analytics_automation_enabled', False)
    monkeypatch.setenv('ANALYTICS_SIGNALS_ENABLED', 'false')
    monkeypatch.setenv('ANALYTICS_AUTOMATION_ENABLED', 'false')
    config = Config()  # keep Alembic from reconfiguring pytest/global logging
    config.set_main_option('script_location', str(BACKEND/'alembic'))
    try:
        command.upgrade(config, '20261022_24')
        yield engine, schema
    finally:
        engine.dispose()
        with admin.begin() as conn: conn.execute(sa.text('DROP SCHEMA '+schema+' CASCADE'))
        admin.dispose()


def old_metadata():
    metadata = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in NEW_TABLES | CONTROL_TABLES: table.to_metadata(metadata)
    return metadata


def snapshot(engine):
    """Tiny synthetic tables only; never suitable for the production database."""
    with engine.connect() as conn:
        inspector = sa.inspect(conn)
        names = set(inspector.get_table_names()) - NEW_TABLES - {'alembic_version'}
        quote = conn.dialect.identifier_preparer.quote
        return {name: conn.scalar(sa.text('SELECT coalesce(jsonb_agg(j ORDER BY j::text),\'[]\'::jsonb)::text '
            'FROM (SELECT to_jsonb(t) j FROM '+quote(name)+' t) s')) for name in names}


def test_pg_real24_to25_noop_readonly_start_and_old_models(pg24, monkeypatch):
    engine, schema = pg24
    with engine.begin() as conn:
        conn.execute(sa.text('UPDATE analytics_automation_policy SET approved=true,settings_json=:fixture'),
                     {'fixture': '{"fixture":true}'})
    before = snapshot(engine)
    # Current runtime refuses24 before starting; no implicit schema change.
    with pytest.raises(launcher.StartupBlocked, match='revision differs'):
        launcher.check_schema(engine, LEGACY_METADATA, '20261030_25')
    launcher.check_schema(engine, old_metadata(), '20261022_24')
    launcher.migrate(engine, '20261030_25', LEGACY_METADATA)
    assert snapshot(engine) == before
    with engine.connect() as conn:
        assert launcher.revision(conn) == '20261030_25'
        assert all(conn.scalar(sa.text('SELECT count(*) FROM '+name)) == 0 for name in NEW_TABLES)
    statements = []
    sa.event.listen(engine, 'before_cursor_execute', lambda c,cur,sql,p,ctx,m: statements.append(sql))
    launcher.check_schema(engine, LEGACY_METADATA, '20261030_25')
    # Even an old image lacking all new models must validate the physical25 extension.
    launcher.check_schema(engine, old_metadata(), '20261030_25')
    assert not any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER','DROP','TRUNCATE'))
                   for sql in statements)
    monkeypatch.setattr(launcher.subprocess, 'run', lambda *a,**k: pytest.fail('25→25 must not invoke Alembic'))
    launcher.migrate(engine, '20261030_25', LEGACY_METADATA)
    assert snapshot(engine) == before
    with pytest.raises(launcher.StartupBlocked, match='Unreviewed migration transition'):
        launcher.migrate(engine, '20261022_24')


@pytest.mark.parametrize('mutation,message', [
    ('DROP TABLE churn_receipts', 'Missing required table'),
    ('ALTER TABLE churn_jobs DROP COLUMN error_code', 'column contract'),
    ('ALTER TABLE churn_receipts ALTER COLUMN event_id TYPE integer', 'column contract'),
    ('ALTER TABLE churn_schedule ALTER COLUMN enabled DROP NOT NULL', 'column contract'),
    ('ALTER TABLE churn_jobs DROP CONSTRAINT uq_churn_job_day_kind', 'unique constraint'),
    ('ALTER TABLE churn_receipts DROP CONSTRAINT uq_churn_receipt_event', 'unique constraint'),
    ('ALTER TABLE churn_jobs DROP CONSTRAINT churn_jobs_release_id_fkey', 'foreign key'),
    ('ALTER TABLE churn_receipts DROP CONSTRAINT churn_receipts_job_id_fkey', 'foreign key'),
    ('ALTER TABLE churn_receipts ALTER COLUMN created_for_release SET DEFAULT true', 'default mismatch'),
    ('ALTER TABLE churn_receipts DROP CONSTRAINT churn_receipts_pkey', 'primary key'),
    ("UPDATE alembic_version SET version_num='20261101_26'", 'Unsupported'),
    ("INSERT INTO alembic_version VALUES('20261022_24')", 'Unsupported'),
])
def test_pg_contract_drift_refuses_even_old_models(pg24, mutation, message):
    engine, schema = pg24
    launcher.migrate(engine, '20261030_25')
    with engine.begin() as conn: conn.execute(sa.text(mutation))
    with pytest.raises(launcher.StartupBlocked, match=message):
        launcher.check_schema(engine, old_metadata(), '20261030_25')


@pytest.mark.parametrize('clause', ['NOT VALID', 'DEFERRABLE INITIALLY DEFERRED', 'ON DELETE CASCADE'])
def test_pg_nonstandard_foreign_key_is_blocked(pg24, clause):
    engine, schema = pg24
    launcher.migrate(engine, '20261030_25')
    with engine.begin() as conn:
        conn.execute(sa.text('ALTER TABLE churn_receipts DROP CONSTRAINT churn_receipts_job_id_fkey'))
        conn.execute(sa.text('ALTER TABLE churn_receipts ADD CONSTRAINT churn_receipts_job_id_fkey '
            'FOREIGN KEY(job_id) REFERENCES churn_jobs(id) '+clause))
    with pytest.raises(launcher.StartupBlocked): launcher.check_schema(engine, old_metadata(), '20261030_25')


def test_pg_foreign_key_other_schema_and_competing_migration(pg24, monkeypatch):
    engine, schema = pg24
    with engine.connect() as lock:
        lock.execute(sa.text('SELECT pg_advisory_lock(:key)'), {'key': launcher.SCHEMA_LOCK});lock.commit()
        with pytest.raises(launcher.StartupBlocked, match='Another schema job'):
            launcher.migrate(engine, '20261030_25')
        lock.execute(sa.text('SELECT pg_advisory_unlock(:key)'), {'key': launcher.SCHEMA_LOCK});lock.commit()
    launcher.migrate(engine, '20261030_25')
    # An FK pointing to a same-named table in another schema is not the contract.
    other = schema+'_other'
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE SCHEMA '+other))
        conn.execute(sa.text('CREATE TABLE '+other+'.churn_jobs(id varchar(36) PRIMARY KEY)'))
        conn.execute(sa.text('ALTER TABLE churn_receipts DROP CONSTRAINT churn_receipts_job_id_fkey'))
        conn.execute(sa.text('ALTER TABLE churn_receipts ADD FOREIGN KEY(job_id) REFERENCES '+other+'.churn_jobs(id)'))
    try:
        with pytest.raises(launcher.StartupBlocked, match='foreign key'):
            launcher.check_schema(engine, old_metadata(), '20261030_25')
    finally:
        with engine.begin() as conn: conn.execute(sa.text('DROP SCHEMA '+other+' CASCADE'))


def test_pg_cli_start_and_rollback_leave_schema_and_data_unchanged(pg24, tmp_path):
    engine, schema = pg24
    launcher.migrate(engine, '20261030_25')
    launcher.migrate(engine, '20261109_26', Base.metadata)
    before = snapshot(engine)
    env = dict(os.environ)
    env['PATH'] = str(Path(sys.executable).parent)+os.pathsep+env.get('PATH', '')
    def cli(*args):
        return subprocess.run([sys.executable, '-B', str(BACKEND/'start_backend.py'), *args], cwd=BACKEND,
            env=env, capture_output=True, text=True, timeout=30)
    result = cli('--check-only')
    assert result.returncode == 0 and 'SCHEMA_PREFLIGHT_OK expected=20261109_26' in result.stdout
    # Exercise actual exec handoff without importing/running business schedulers.
    executable = tmp_path/'uvicorn'
    executable.write_text('#!'+sys.executable+'\nimport json,sys\nprint("FIXTURE_EXEC "+json.dumps(sys.argv[1:]))\n')
    executable.chmod(0o755)
    env['PATH'] = str(tmp_path)+os.pathsep+env['PATH']
    assert 'FIXTURE_EXEC' in cli().stdout
    result = cli('--rollback', '--expected-revision', '20261109_26')
    assert result.returncode == 0 and '"--lifespan", "off"' in result.stdout
    assert snapshot(engine) == before
    with engine.begin() as conn: conn.execute(sa.text('ALTER TABLE churn_receipts DROP CONSTRAINT uq_churn_receipt_event'))
    result = cli()
    assert result.returncode == 1 and 'STARTUP_BLOCKED' in result.stderr and 'FIXTURE_EXEC' not in result.stdout


def test_pg_23_requires_separate_reviewed24_step(pg24):
    engine, schema = pg24
    config = Config()  # keep Alembic from reconfiguring pytest/global logging
    config.set_main_option('script_location', str(BACKEND/'alembic'))
    command.downgrade(config, '20261020_23')  # disposable fixture only
    with pytest.raises(launcher.StartupBlocked, match='Unreviewed migration transition'):
        launcher.migrate(engine, '20261030_25')
    with engine.connect() as conn: assert launcher.revision(conn) == '20261020_23'
    launcher.migrate(engine, '20261022_24')
    launcher.check_schema(engine, old_metadata(), '20261022_24')
    launcher.migrate(engine, '20261030_25', LEGACY_METADATA)


@pytest.mark.parametrize('child_succeeds_without_migrating', [False, True])
def test_pg_failed_or_ineffective_alembic_never_reports_success_and_releases_lock(pg24, monkeypatch, child_succeeds_without_migrating):
    engine, schema = pg24
    def child(*args, **kwargs):
        if not child_succeeds_without_migrating:
            raise subprocess.CalledProcessError(1, 'synthetic-alembic-failure')
    monkeypatch.setattr(launcher.subprocess, 'run', child)
    with pytest.raises((launcher.StartupBlocked, subprocess.CalledProcessError)):
        launcher.migrate(engine, '20261030_25')
    with engine.connect() as conn:
        assert launcher.revision(conn) == '20261022_24'
        assert not (NEW_TABLES & set(sa.inspect(conn).get_table_names()))
        assert conn.scalar(sa.text('SELECT pg_try_advisory_lock(:key)'), {'key': launcher.SCHEMA_LOCK})
        conn.execute(sa.text('SELECT pg_advisory_unlock(:key)'), {'key': launcher.SCHEMA_LOCK})


@pytest.mark.skipif(os.environ.get('SCHEMA25_COMPOSE_TESTS') != '1', reason='Explicit local Compose client test opt-in')
@pytest.mark.parametrize('file', ['migrate.churn25.explicit.override.yml', 'rollback.churn25.checked.override.yml', 'migrate.import26.explicit.override.yml', 'rollback.import26.checked.override.yml'])
def test_compose_templates_preserve_db_frontend_volumes_and_disable_automation(file, tmp_path):
    import json
    env = dict(os.environ)
    for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
        env.pop(key, None)
    env.update(POSTGRES_PASSWORD='synthetic-compose-only', SALES_BACKEND_GID='1000',
        SALES_CANDIDATE_IMAGE='schema25-candidate:synthetic', SALES_ROLLBACK_IMAGE='schema24-old:synthetic',
        SALES_ROLLBACK_LAUNCHER=str(BACKEND/'start_backend.py'))
    repo = BACKEND.parent
    base = ['docker','--host=unix:///var/run/docker.sock','compose','--env-file','/dev/null','-f',str(repo/'docker-compose.yml')]
    def config(extra):
        result = subprocess.run(base+extra+['config','--format','json'], env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0  # never echo merged private environment/config
        return json.loads(result.stdout)
    initial = config([])
    updated = config(['-f',str(repo/'deploy'/file)])
    if file.startswith('migrate'):
        assert 'schema-migrate' not in updated['services']
        updated = config(['-f',str(repo/'deploy'/file),'--profile','schema-maintenance'])
    for service in ('db','frontend'):
        assert updated['services'][service] == initial['services'][service]
    assert updated['volumes'] == initial['volumes']
    assert updated['networks'] == initial['networks']
    service = updated['services']['schema-migrate' if file.startswith('migrate') else 'backend']
    assert service['command'][-1] == ('20261109_26' if 'import26' in file else '20261030_25')
    assert service['environment']['ANALYTICS_AUTOMATION_ENABLED'] == 'false'
    assert service['environment']['ANALYTICS_SIGNALS_ENABLED'] == 'false'
    assert service['pull_policy'] == 'never'
    if file.startswith('migrate'):
        assert service['profiles'] == ['schema-maintenance'] and service['restart'] == 'no'
        assert '--migrate' in service['command']
    else:
        assert not service.get('build') and '--rollback' in service['command']
        assert any(v.get('target') == '/run/sales-rollback/start_backend.py' and v.get('read_only') is True
                   for v in service['volumes'])
