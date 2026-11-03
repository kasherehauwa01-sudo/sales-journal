"""Explicit schema job or read-only checked application start; also mountable in old images."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

# A mounted launcher must import the OLD image's /app, never the new checkout.
app_root = Path(__file__).parent
if not (app_root / "app").is_dir():
    app_root = Path("/app")
sys.path.insert(0, str(app_root))

import sqlalchemy as sa

SUPPORTED_REVISIONS = {'20261020_23', '20261022_24', '20261030_25'}
DEFAULT_REVISION = '20261030_25'
MIGRATION_SOURCES = {
    '20261022_24': {'20261020_23', '20261022_24'},
    '20261030_25': {'20261022_24', '20261030_25'},
}
SCHEMA_LOCK = 734190249


class StartupBlocked(RuntimeError):
    pass


def disabled(settings):
    if (getattr(settings, 'analytics_signals_enabled', False)
            or getattr(settings, 'analytics_automation_enabled', False)
            or getattr(settings, 'analytics_signals_mode', 'SHADOW') != 'SHADOW'):
        raise StartupBlocked('Rollback/migration requires disabled SHADOW and automation')


def sync_engine(settings):
    url = sa.engine.make_url(settings.database_url)
    if url.drivername not in {'postgresql+asyncpg', 'postgresql+psycopg'}:
        raise StartupBlocked('PostgreSQL required')
    return sa.create_engine(url.set(drivername='postgresql+psycopg'), poolclass=sa.pool.NullPool,
                            connect_args={'connect_timeout': 5})


def revision(conn):
    values = list(conn.execute(sa.text('SELECT version_num FROM alembic_version')).scalars())
    if len(values) != 1 or values[0] not in SUPPORTED_REVISIONS:
        raise StartupBlocked('Unsupported or branched database revision')
    return values[0]


def type_name(column_type, dialect):
    return str(column_type.compile(dialect=dialect)).upper().replace(' ', '')


# Frozen physical DDL contract, not imports from the candidate's ORM. The same
# launcher can be mounted read-only in an old image which has no churn models.
CHURN_COLUMNS = {
    'churn_schedule': {
        'id': ('INTEGER', False), 'revision': ('INTEGER', False),
        'approved': ('BOOLEAN', False), 'enabled': ('BOOLEAN', False), 'settings_json': ('JSON', False),
    },
    'churn_jobs': {
        'id': ('VARCHAR(36)', False), 'business_date': ('DATE', False), 'kind': ('VARCHAR(16)', False),
        'policy_revision': ('INTEGER', False), 'status': ('VARCHAR(32)', False),
        'run_id': ('VARCHAR(36)', True), 'release_id': ('VARCHAR(36)', True),
        'created_at': ('TIMESTAMPWITHTIMEZONE', False), 'updated_at': ('TIMESTAMPWITHTIMEZONE', False),
        'counters': ('JSON', False), 'error_code': ('VARCHAR(64)', True),
    },
    'churn_receipts': {
        'id': ('VARCHAR(36)', False), 'job_id': ('VARCHAR(36)', False), 'event_id': ('BIGINT', False),
        'event_hash': ('VARCHAR(64)', False), 'decision': ('VARCHAR(32)', False), 'task_id': ('VARCHAR(64)', True),
        'created_for_release': ('BOOLEAN', False), 'reasons': ('JSON', False),
        'observed_at': ('TIMESTAMPWITHTIMEZONE', False),
    },
}
CHURN_UNIQUE = {
    'churn_jobs': ('uq_churn_job_day_kind', ['business_date', 'kind']),
    'churn_receipts': ('uq_churn_receipt_event', ['job_id', 'event_id']),
}
CHURN_FOREIGN_KEYS = {
    'churn_jobs': ('release_id', 'analytics_automation_releases'),
    'churn_receipts': ('job_id', 'churn_jobs'),
}


def check_churn_schema(conn, schema, inspector):
    tables = set(inspector.get_table_names(schema=schema))
    for table, columns in CHURN_COLUMNS.items():
        if table not in tables:
            raise StartupBlocked('Missing required table: '+table)
        actual = {c['name']: c for c in inspector.get_columns(table, schema=schema)}
        for name, (expected_type, nullable) in columns.items():
            found = actual.get(name)
            actual_type = type_name(found['type'], conn.dialect) if found else None
            if (not found or not (actual_type == expected_type or (expected_type == 'JSON' and actual_type == 'JSONB'))
                    or found['nullable'] != nullable):
                raise StartupBlocked('Revision25 column contract mismatch: '+table+'.'+name)
        if inspector.get_pk_constraint(table, schema=schema)['constrained_columns'] != ['id']:
            raise StartupBlocked('Revision25 primary key mismatch: '+table)
        if table in CHURN_UNIQUE:
            name, names = CHURN_UNIQUE[table]
            if not any(c['name'] == name and c['column_names'] == names
                       for c in inspector.get_unique_constraints(table, schema=schema)):
                raise StartupBlocked('Revision25 unique constraint mismatch: '+table)
        if table in CHURN_FOREIGN_KEYS:
            name, target = CHURN_FOREIGN_KEYS[table]
            if not any(fk['constrained_columns'] == [name] and fk['referred_table'] == target
                       and fk['referred_columns'] == ['id'] and fk.get('referred_schema') in {None, schema}
                       and fk.get('options', {}).get('ondelete') in {None, 'NO ACTION'}
                       and fk.get('options', {}).get('onupdate') in {None, 'NO ACTION'}
                       for fk in inspector.get_foreign_keys(table, schema=schema)):
                raise StartupBlocked('Revision25 foreign key mismatch: '+table)
    created_default = next(c for c in inspector.get_columns('churn_receipts', schema=schema)
                           if c['name'] == 'created_for_release')['default']
    if str(created_default).lower().replace(' ', '').strip('()') not in {'false', 'false::boolean'}:
        raise StartupBlocked('Revision25 receipt default mismatch')
    constraints = conn.execute(sa.text('''
        SELECT c.convalidated, c.condeferrable, c.condeferred
        FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname=:schema AND t.relname IN ('churn_schedule','churn_jobs','churn_receipts')
          AND c.contype IN ('p','u','f')
    '''), {'schema': schema})
    if any(not row.convalidated or row.condeferrable or row.condeferred for row in constraints):
        raise StartupBlocked('Revision25 constraint is unvalidated or deferrable')


def check_schema(engine, metadata, expected):
    if expected not in SUPPORTED_REVISIONS:
        raise StartupBlocked('Unreviewed expected revision')
    with engine.connect() as conn:
        _check_schema(conn, metadata, expected)


def _check_schema(conn, metadata, expected):
    with conn.begin():
        conn.execute(sa.text('SET TRANSACTION READ ONLY'))
        conn.execute(sa.text("SET LOCAL statement_timeout='5s'"))
        conn.execute(sa.text("SET LOCAL lock_timeout='1s'"))
        conn.execute(sa.text('SELECT pg_advisory_xact_lock_shared(:key)'), {'key': SCHEMA_LOCK})
        if conn.scalar(sa.text('SHOW transaction_read_only')) != 'on':
            raise StartupBlocked('Read-only preflight required')
        current = revision(conn)
        if current != expected:
            raise StartupBlocked('Database revision differs from explicit expected revision: '+current+' != '+expected+
                                 '; schema changes require a separately approved --migrate job')
        schema = conn.scalar(sa.text('SELECT current_schema()'))
        inspector = sa.inspect(conn)
        tables = set(inspector.get_table_names(schema=schema))
        for table in metadata.sorted_tables:
            if table.name not in tables:
                raise StartupBlocked('Missing required table: '+table.name)
            actual = {c['name']: c for c in inspector.get_columns(table.name, schema=schema)}
            for column in table.columns:
                found = actual.get(column.name)
                expected_type = type_name(column.type, conn.dialect)
                actual_type = type_name(found['type'], conn.dialect) if found else None
                # Historical report migrations use JSONB with generic JSON ORM fields.
                compatible = actual_type == expected_type or (expected_type == 'JSON' and actual_type == 'JSONB')
                if not found or not compatible:
                    raise StartupBlocked('Column contract mismatch: '+table.name+'.'+column.name)
                # ORM nullability hints are not the historical DDL contract:
                # revision23 has nullable import_batches.uploaded_at, for example.
                # Check the physical PK/FK and types, not inferred NOT NULL hints.
            pk = inspector.get_pk_constraint(table.name, schema=schema)['constrained_columns']
            if pk != [c.name for c in table.primary_key]:
                raise StartupBlocked('Primary key mismatch: '+table.name)
            unique = {tuple(x['column_names']) for x in inspector.get_unique_constraints(table.name, schema=schema)}
            unique.update(tuple(x['column_names']) for x in inspector.get_indexes(table.name, schema=schema) if x['unique'])
            required_unique = [tuple(c.name for c in x.columns) for x in table.constraints if isinstance(x, sa.UniqueConstraint)]
            required_unique += [tuple(c.name for c in x.columns) for x in table.indexes if x.unique]
            if any(columns not in unique for columns in required_unique):
                raise StartupBlocked('Unique constraint mismatch: '+table.name)
            actual_fks = inspector.get_foreign_keys(table.name, schema=schema)
            for fk in table.foreign_key_constraints:
                names = [e.parent.name for e in fk.elements]
                target = next(iter(fk.elements)).column.table.name
                columns = [e.column.name for e in fk.elements]
                if not any(x['constrained_columns'] == names and x['referred_table'] == target
                           and x['referred_columns'] == columns for x in actual_fks):
                    raise StartupBlocked('Foreign key mismatch: '+table.name)
        if expected == DEFAULT_REVISION:
            check_churn_schema(conn, schema, inspector)


def migrate(engine, target, metadata=None):
    if target not in MIGRATION_SOURCES:
        raise StartupBlocked('Only reviewed migration targets 20261022_24 and 20261030_25 are supported')
    with engine.connect() as conn:
        acquired = conn.scalar(sa.text('SELECT pg_try_advisory_lock(:key)'), {'key': SCHEMA_LOCK})
        conn.commit()
        if not acquired:
            raise StartupBlocked('Another schema job is running')
        try:
            conn.execute(sa.text("SET LOCAL statement_timeout='5s'"))
            conn.execute(sa.text("SET LOCAL lock_timeout='1s'"))
            current = revision(conn)
            conn.commit()
            if current not in MIGRATION_SOURCES[target]:
                raise StartupBlocked('Unreviewed migration transition: '+current+' -> '+target)
            if current != target:
                env = dict(os.environ)
                # Defaults for the explicit job; preserve deliberate operator overrides.
                env['PGOPTIONS'] = '-c lock_timeout=2000 -c statement_timeout=30000 '+env.get('PGOPTIONS', '')
                subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', target], check=True, env=env, cwd=app_root)
            # Verify the actual resulting revision and schema, including all revision25
            # contracts even when a mounted launcher imports older ORM models.
            # Reuse this session: a second connection would wait on our own schema lock.
            _check_schema(conn, metadata if metadata is not None else sa.MetaData(), target)
        finally:
            conn.rollback()
            conn.execute(sa.text('SELECT pg_advisory_unlock(:key)'), {'key': SCHEMA_LOCK})
            conn.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-revision', default=DEFAULT_REVISION, choices=sorted(SUPPORTED_REVISIONS))
    parser.add_argument('--rollback', action='store_true', help='Disable all lifespan schedulers; requires flags OFF')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check-only', action='store_true')
    mode.add_argument('--migrate', action='store_true', help='EXPLICIT write job, never part of normal start')
    args = parser.parse_args(argv)
    from app.config import settings
    from app.models import Base
    if args.rollback or args.migrate:
        disabled(settings)
    if args.rollback and args.migrate:
        raise StartupBlocked('Rollback must never migrate')
    engine = sync_engine(settings)
    try:
        if args.migrate:
            migrate(engine, args.expected_revision, Base.metadata if args.expected_revision == DEFAULT_REVISION else None)
            print('SCHEMA_MIGRATION_OK expected='+args.expected_revision, flush=True)
            return
        check_schema(engine, Base.metadata, args.expected_revision)
    finally:
        engine.dispose()
    print('SCHEMA_PREFLIGHT_OK expected='+args.expected_revision+' rollback='+str(args.rollback), flush=True)
    if args.check_only:
        return
    command = ['uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000',
               '--proxy-headers', '--forwarded-allow-ips=*']
    if args.rollback:
        command += ['--lifespan', 'off', '--timeout-graceful-shutdown', '20']
    os.execvp(command[0], command)


if __name__ == '__main__':
    try:
        main()
    except (StartupBlocked, sa.exc.SQLAlchemyError, subprocess.CalledProcessError) as error:
        message = str(error) if isinstance(error, StartupBlocked) else type(error).__name__
        print('STARTUP_BLOCKED: '+message, file=sys.stderr)
        sys.exit(1)
