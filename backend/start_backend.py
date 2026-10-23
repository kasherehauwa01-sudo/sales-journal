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

SUPPORTED_REVISIONS = {'20261020_23', '20261022_24'}
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


def check_schema(engine, metadata, expected):
    if expected not in SUPPORTED_REVISIONS:
        raise StartupBlocked('Unreviewed expected revision')
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(sa.text('SET TRANSACTION READ ONLY'))
            conn.execute(sa.text("SET LOCAL statement_timeout='5s'"))
            conn.execute(sa.text("SET LOCAL lock_timeout='1s'"))
            conn.execute(sa.text('SELECT pg_advisory_xact_lock_shared(:key)'), {'key': SCHEMA_LOCK})
            if conn.scalar(sa.text('SHOW transaction_read_only')) != 'on':
                raise StartupBlocked('Read-only preflight required')
            if revision(conn) != expected:
                raise StartupBlocked('Database revision differs from explicit expected revision')
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
    # No Alembic, create_all, stamp, UPDATE, nextval or business scheduler is run.


def migrate(engine, target):
    if target != '20261022_24':
        raise StartupBlocked('Only reviewed target 20261022_24 is supported')
    with engine.connect() as conn:
        acquired = conn.scalar(sa.text('SELECT pg_try_advisory_lock(:key)'), {'key': SCHEMA_LOCK})
        conn.commit()
        if not acquired:
            raise StartupBlocked('Another schema job is running')
        try:
            revision(conn)
            conn.commit()
            env = dict(os.environ)
            # Defaults for the explicit job; preserve deliberate operator PGOPTIONS overrides.
            env['PGOPTIONS'] = '-c lock_timeout=2000 -c statement_timeout=30000 '+env.get('PGOPTIONS', '')
            subprocess.run(['alembic', 'upgrade', target], check=True, env=env)
        finally:
            conn.execute(sa.text('SELECT pg_advisory_unlock(:key)'), {'key': SCHEMA_LOCK})
            conn.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-revision', default='20261022_24', choices=sorted(SUPPORTED_REVISIONS))
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
            migrate(engine, args.expected_revision)
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
