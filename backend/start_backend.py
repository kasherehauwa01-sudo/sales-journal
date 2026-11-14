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

SUPPORTED_REVISIONS = {'20261020_23', '20261022_24', '20261030_25', '20261109_26', '20261111_27', '20261112_28', '20261114_29'}
DEFAULT_REVISION = '20261114_29'
MIGRATION_SOURCES = {
    '20261022_24': {'20261020_23', '20261022_24'},
    '20261030_25': {'20261022_24', '20261030_25'},
    '20261109_26': {'20261030_25', '20261109_26'},
    '20261111_27': {'20261109_26', '20261111_27'},
    '20261112_28': {'20261111_27', '20261112_28'},
    '20261114_29': {'20261112_28', '20261114_29'},
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


IMPORT_CONTROL_CONTRACT = {
    "import_control_audit": {
        "columns": {
            "id": ("BIGINT", False),
            "entity": ("VARCHAR(32)", False),
            "entity_id": ("VARCHAR(64)", False),
            "actor": ("VARCHAR(255)", False),
            "effective_date": ("DATE", True),
            "changes": ("JSON", False),
            "created_at": ("TIMESTAMPWITHTIMEZONE", False),
        },
        "pk": ["id"],
        "unique": [],
        "fk": [],
    },
    "import_control_config": {
        "columns": {
            "id": ("INTEGER", False),
            "enabled": ("BOOLEAN", False),
            "automatic": ("BOOLEAN", False),
            "email_enabled": ("BOOLEAN", False),
            "start_date": ("DATE", True),
            "day_offset": ("INTEGER", True),
            "marker_template": ("VARCHAR(512)", True),
            "recipients": ("TEXT", False),
            "revision": ("INTEGER", False),
            "created_at": ("TIMESTAMPWITHTIMEZONE", False),
            "updated_at": ("TIMESTAMPWITHTIMEZONE", False),
        },
        "pk": ["id"],
        "unique": [],
        "fk": [],
    },
    "import_control_cycles": {
        "columns": {
            "id": ("VARCHAR(36)", False),
            "sales_date": ("DATE", True),
            "status": ("VARCHAR(24)", False),
            "source_confirmed": ("BOOLEAN", False),
            "config_revision": ("INTEGER", True),
            "started_at": ("TIMESTAMPWITHTIMEZONE", False),
            "finished_at": ("TIMESTAMPWITHTIMEZONE", True),
            "error_code": ("VARCHAR(64)", True),
        },
        "pk": ["id"],
        "unique": [],
        "fk": [],
    },
    "import_control_notices": {
        "columns": {
            "id": ("BIGINT", False),
            "sales_date": ("DATE", False),
            "dedupe_key": ("VARCHAR(64)", False),
            "status": ("VARCHAR(24)", False),
            "attempts": ("INTEGER", False),
            "recipients": ("TEXT", False),
            "subject": ("VARCHAR(255)", False),
            "body": ("TEXT", False),
            "error_code": ("VARCHAR(64)", True),
            "next_attempt": ("TIMESTAMPWITHTIMEZONE", False),
            "created_at": ("TIMESTAMPWITHTIMEZONE", False),
            "sent_at": ("TIMESTAMPWITHTIMEZONE", True),
        },
        "pk": ["id"],
        "unique": [["dedupe_key"]],
        "fk": [],
    },
    "import_day_checks": {
        "columns": {
            "sales_date": ("DATE", False),
            "status": ("VARCHAR(32)", False),
            "expected": ("INTEGER", False),
            "successful": ("INTEGER", False),
            "missing": ("INTEGER", False),
            "errors": ("INTEGER", False),
            "checked_at": ("TIMESTAMPWITHTIMEZONE", False),
            "config_revision": ("INTEGER", False),
            "error_code": ("VARCHAR(64)", True),
        },
        "pk": ["sales_date"],
        "unique": [],
        "fk": [],
    },
    "import_departments": {
        "columns": {
            "id": ("BIGINT", False),
            "code": ("VARCHAR(128)", False),
            "name": ("VARCHAR(255)", False),
            "created_at": ("TIMESTAMPWITHTIMEZONE", False),
            "updated_at": ("TIMESTAMPWITHTIMEZONE", False),
        },
        "pk": ["id"],
        "unique": [["code"]],
        "fk": [],
    },
    "import_control_receipts": {
        "columns": {
            "id": ("BIGINT", False),
            "import_id": ("BIGINT", False),
            "cycle_id": ("VARCHAR(36)", True),
            "filename": ("VARCHAR(512)", False),
            "sha256": ("VARCHAR(64)", True),
            "status": ("VARCHAR(32)", False),
            "period_start": ("DATE", True),
            "period_end": ("DATE", True),
            "period_method": ("VARCHAR(64)", True),
            "departments": ("JSON", False),
            "error_code": ("VARCHAR(64)", True),
            "duplicate_of": ("BIGINT", True),
            "created_at": ("TIMESTAMPWITHTIMEZONE", False),
            "finished_at": ("TIMESTAMPWITHTIMEZONE", True),
        },
        "pk": ["id"],
        "unique": [["import_id"]],
        "fk": [
            ["import_id", "import_batches", "id"],
            ["cycle_id", "import_control_cycles", "id"],
            ["duplicate_of", "import_control_receipts", "id"],
        ],
    },
    "import_expectation_rules": {
        "columns": {
            "id": ("BIGINT", False),
            "department_id": ("BIGINT", False),
            "start_date": ("DATE", False),
            "end_date": ("DATE", True),
            "filename_template": ("VARCHAR(512)", False),
            "date_format": ("VARCHAR(8)", False),
            "display_name": ("VARCHAR(255)", False),
            "active": ("BOOLEAN", False),
            "controlled": ("BOOLEAN", False),
            "created_at": ("TIMESTAMPWITHTIMEZONE", False),
            "updated_at": ("TIMESTAMPWITHTIMEZONE", False),
        },
        "pk": ["id"],
        "unique": [],
        "fk": [["department_id", "import_departments", "id"]],
    },
    "import_control_issues": {
        "columns": {
            "id": ("BIGINT", False),
            "sales_date": ("DATE", False),
            "department_id": ("BIGINT", False),
            "rule_id": ("BIGINT", False),
            "expected_filename": ("VARCHAR(512)", False),
            "department_name": ("VARCHAR(255)", False),
            "status": ("VARCHAR(24)", False),
            "error_code": ("VARCHAR(64)", True),
            "import_id": ("BIGINT", True),
            "detected_at": ("TIMESTAMPWITHTIMEZONE", False),
            "checked_at": ("TIMESTAMPWITHTIMEZONE", False),
            "resolved_at": ("TIMESTAMPWITHTIMEZONE", True),
            "closure_reason": ("TEXT", True),
            "closure_method": ("VARCHAR(32)", True),
        },
        "pk": ["id"],
        "unique": [["sales_date", "department_id"]],
        "fk": [
            ["rule_id", "import_expectation_rules", "id"],
            ["import_id", "import_batches", "id"],
            ["department_id", "import_departments", "id"],
        ],
    },
}


IMPORT_HISTORY_CONTRACT = {
    "import_history_job": {
        "columns": {
            "id": ("INTEGER", False),
            "status": ("VARCHAR(24)", False),
            "plan": ("JSON", False),
            "rules_hash": ("VARCHAR(64)", False),
            "position": ("INTEGER", False),
            "next_date": ("DATE", True),
            "checked": ("INTEGER", False),
            "total": ("INTEGER", False),
            "error_code": ("VARCHAR(64)", True),
            "started_at": ("TIMESTAMPWITHTIMEZONE", False),
            "updated_at": ("TIMESTAMPWITHTIMEZONE", False),
        },
        "pk": ["id"],
        "unique": [],
        "fk": [],
    },
    "import_history_results": {
        "columns": {
            "sales_date": ("DATE", False),
            "department_id": ("BIGINT", False),
            "rule_id": ("BIGINT", True),
            "expected_filename": ("VARCHAR(512)", True),
            "status": ("VARCHAR(24)", False),
            "error_code": ("VARCHAR(64)", True),
            "checked_at": ("TIMESTAMPWITHTIMEZONE", False),
        },
        "pk": ["sales_date", "department_id"],
        "unique": [],
        "fk": [
            ("department_id", "import_departments", "id"),
            ("rule_id", "import_expectation_rules", "id"),
        ],
    },
}


def check_import_control_schema(conn, schema, inspector, historical=False):
    contracts = {
        **IMPORT_CONTROL_CONTRACT,
        **(IMPORT_HISTORY_CONTRACT if historical else {}),
    }
    for name, contract in contracts.items():
        columns = {c["name"]: c for c in inspector.get_columns(name, schema=schema)}
        for key, (typ, nullable) in contract["columns"].items():
            if (
                key not in columns
                or type_name(columns[key]["type"], conn.dialect) != typ
                or columns[key]["nullable"] != nullable
            ):
                raise StartupBlocked("Revision26 column mismatch: " + name + "." + key)
        if (
            inspector.get_pk_constraint(name, schema=schema)["constrained_columns"]
            != contract["pk"]
        ):
            raise StartupBlocked("Revision26 primary key mismatch: " + name)
        unique = [
            c["column_names"]
            for c in inspector.get_unique_constraints(name, schema=schema)
        ]
        if any(keys not in unique for keys in contract["unique"]):
            raise StartupBlocked("Revision26 unique mismatch: " + name)
        fks = inspector.get_foreign_keys(name, schema=schema)
        for field, target, key in contract["fk"]:
            if not any(
                f["constrained_columns"] == [field]
                and f["referred_table"] == target
                and f["referred_columns"] == [key]
                and f.get("referred_schema") in (None, schema)
                and f.get("options", {}).get("ondelete") == "RESTRICT"
                for f in fks
            ):
                raise StartupBlocked("Revision26 foreign key mismatch: " + name)
    required_checks = {
        'import_control_config': {
            'day_offset IS NULL OR day_offset >= 0 AND day_offset <= 7', 'id = 1'},
        'import_expectation_rules': {
            'end_date IS NULL OR end_date >= start_date',
            "date_format::text = ANY (ARRAY['dmy'::character varying, 'iso'::character varying]::text[])"},
    }
    for name, checks in required_checks.items():
        actual = {''.join(c['sqltext'].split()) for c in inspector.get_check_constraints(name, schema=schema)}
        if not {''.join(c.split()) for c in checks}.issubset(actual):
            raise StartupBlocked('Revision26 check constraint mismatch: '+name)
    invalid = conn.scalar(sa.text("""SELECT count(*) FROM pg_constraint c
        JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname=:schema AND t.relname=ANY(:tables)
        AND (NOT c.convalidated OR c.condeferrable OR c.condeferred)"""),
        {'schema':schema,'tables':list(contracts)})
    if invalid:
        raise StartupBlocked('Revision26 constraint is unvalidated or deferrable')
    if historical:
        required_history_checks = {
            'import_history_job': ["id=1", "position>=0 AND checked>=0 AND total>=checked",
                "status::text = ANY (ARRAY['running'::character varying, 'paused'::character varying, 'completed'::character varying, 'error'::character varying]::text[])"],
            'import_history_results': ["status::text = ANY (ARRAY['success'::character varying, 'missing'::character varying, 'error'::character varying, 'unavailable'::character varying, 'waiting'::character varying, 'not_expected'::character varying]::text[])"]}
        for table, checks in required_history_checks.items():
            actual = {''.join(c['sqltext'].split()).replace('(', '').replace(')', '').replace(chr(34), '') for c in inspector.get_check_constraints(table, schema=schema)}
            if not {''.join(c.split()).replace('(', '').replace(')', '') for c in checks}.issubset(actual):
                raise StartupBlocked('Revision27 check constraint mismatch: '+table)
        paused_default = next(c['default'] for c in inspector.get_columns('import_history_job', schema=schema) if c['name']=='status')
        if str(paused_default) != "'paused'::character varying":
            raise StartupBlocked('Revision27 job must default paused')
        indexes = {tuple(i['column_names']) for i in inspector.get_indexes('import_history_results', schema=schema)}
        if not {('status',), ('expected_filename',), ('department_id', 'sales_date')}.issubset(indexes):
            raise StartupBlocked('Revision27 result indexes missing')
    flags = {
        c["name"]: str(c.get("default", ""))
        .strip("()")
        .replace("'", "")
        .replace("::boolean", "")
        for c in inspector.get_columns("import_control_config", schema=schema)
    }
    if any(flags[key] != "false" for key in ("enabled", "automatic", "email_enabled")):
        raise StartupBlocked("Revision26 control must default OFF")
    triggers = conn.scalar(
        sa.text(
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relname='import_expectation_rules' AND t.tgname='import_rule_guard' AND t.tgenabled='O'"
        ),
        {"schema": schema},
    )
    if triggers != 1:
        raise StartupBlocked("Revision26 rule guard is missing")



SALES_CONTROL_CONTRACT = {'sales_control_settings': {'columns': {'id': ('INTEGER', False), 'enabled': ('BOOLEAN', False), 'send_time': ('VARCHAR(5)', False), 'recipients': ('JSON', False), 'not_before': ('TIMESTAMPWITHTIMEZONE', True), 'revision': ('INTEGER', False)}, 'pk': ['id'], 'unique': [], 'fks': [], 'indexes': []}, 'sales_control_exclusions': {'columns': {'department': ('VARCHAR(512)', False), 'sales_date': ('DATE', False), 'hidden_at': ('TIMESTAMPWITHTIMEZONE', False), 'actor': ('VARCHAR(255)', False)}, 'pk': ['department', 'sales_date'], 'unique': [], 'fks': [], 'indexes': [['sales_date']]}, 'sales_control_audit': {'columns': {'id': ('INTEGER', False), 'created_at': ('TIMESTAMPWITHTIMEZONE', False), 'actor': ('VARCHAR(255)', False), 'action': ('VARCHAR(32)', False), 'details': ('JSON', False)}, 'pk': ['id'], 'unique': [], 'fks': [], 'indexes': []}, 'sales_control_deliveries': {'columns': {'id': ('INTEGER', False), 'settings_id': ('INTEGER', False), 'sales_date': ('DATE', False), 'recipients_hash': ('VARCHAR(64)', False), 'checked_at': ('TIMESTAMPWITHTIMEZONE', False), 'missing_count': ('INTEGER', False), 'status': ('VARCHAR(20)', False), 'attempted_at': ('TIMESTAMPWITHTIMEZONE', True), 'sent_at': ('TIMESTAMPWITHTIMEZONE', True), 'next_attempt': ('TIMESTAMPWITHTIMEZONE', True), 'attempts': ('INTEGER', False), 'error_code': ('VARCHAR(48)', True), 'manual_retry': ('BOOLEAN', False)}, 'pk': ['id'], 'unique': [['sales_date', 'recipients_hash']], 'fks': [(['settings_id'], 'sales_control_settings', ['id'], 'RESTRICT')], 'indexes': [['checked_at']]}}
# Catalog-rendered expressions pinned after local PostgreSQL16 migration tests.
SALES_CONTROL_CHECKS = {'sales_control_settings': {'ck_sales_control_singleton': 'id=1', 'ck_sales_control_time': "send_time::text~'^([01][0-9]|2[0-3]):[0-5][0-9]$'::text"}, 'sales_control_exclusions': {}, 'sales_control_audit': {}, 'sales_control_deliveries': {'ck_sales_control_counts': 'attempts>=0ANDmissing_count>=0', 'ck_sales_control_delivery_status': "status::text=ANY(ARRAY['sending'::charactervarying,'sent'::charactervarying,'failed'::charactervarying,'uncertain'::charactervarying,'not_required'::charactervarying]::text[])"}}

def check_sales_control_schema(conn,schema,inspector):
    for name,contract in SALES_CONTROL_CONTRACT.items():
        columns={c['name']:c for c in inspector.get_columns(name,schema=schema)}
        for col,(typ,nullable) in contract['columns'].items():
            if col not in columns or type_name(columns[col]['type'],conn.dialect)!=typ or columns[col]['nullable']!=nullable:raise StartupBlocked('Revision28 column mismatch: '+name+'.'+col)
        if inspector.get_pk_constraint(name,schema=schema)['constrained_columns']!=contract['pk']:raise StartupBlocked('Revision28 PK mismatch')
        unique={tuple(x['column_names']) for x in inspector.get_unique_constraints(name,schema=schema)}
        if any(tuple(x) not in unique for x in contract['unique']):raise StartupBlocked('Revision28 UNIQUE mismatch')
        for names,target,cols,ondelete in contract['fks']:
            if not any(f['constrained_columns']==names and f['referred_table']==target and f['referred_columns']==cols and f.get('options',{}).get('ondelete')==ondelete for f in inspector.get_foreign_keys(name,schema=schema)):raise StartupBlocked('Revision28 FK mismatch')
        indexes={tuple(x['column_names']) for x in inspector.get_indexes(name,schema=schema)}
        if any(tuple(x) not in indexes for x in contract['indexes']):raise StartupBlocked('Revision28 index mismatch')
        checks={x['name']:''.join(x['sqltext'].split()) for x in inspector.get_check_constraints(name,schema=schema)}
        if checks!=SALES_CONTROL_CHECKS.get(name,{}):raise StartupBlocked('Revision28 CHECK mismatch')
    invalid=conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE connamespace=(SELECT oid FROM pg_namespace WHERE nspname=:schema) AND conrelid::regclass::text=ANY(:names) AND (NOT convalidated OR condeferrable OR condeferred))"),{'schema':schema,'names':list(SALES_CONTROL_CONTRACT)})
    if invalid:raise StartupBlocked('Revision28 invalid constraints')
    defaults={c['name']:str(c.get('default','')) for c in inspector.get_columns('sales_control_settings',schema=schema)}
    if defaults['enabled'] not in ('false','false::boolean'):raise StartupBlocked('Revision28 must default disabled')
    valid=conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname='ix_sales_department_date_control' AND i.indrelid='sales'::regclass AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=2 AND pg_get_indexdef(i.indexrelid,1,true)='department' AND pg_get_indexdef(i.indexrelid,2,true)='sale_date')"))
    if not valid:raise StartupBlocked('Revision28 sales index invalid/missing')


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
            if expected != "20261114_29" and table.name.startswith("category_season_"):continue
            if expected not in ('20261111_27','20261112_28','20261114_29') and table.name in IMPORT_HISTORY_CONTRACT:
                continue
            if expected not in ('20261112_28','20261114_29') and table.name in SALES_CONTROL_CONTRACT:continue
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
        if expected in ('20261030_25', '20261109_26', '20261111_27','20261112_28','20261114_29'):
            check_churn_schema(conn, schema, inspector)
        if expected in ('20261109_26', '20261111_27','20261112_28','20261114_29'):
            check_import_control_schema(conn, schema, inspector, historical=expected in ('20261111_27','20261112_28','20261114_29'))
        if expected in ('20261112_28','20261114_29'):check_sales_control_schema(conn,schema,inspector)
        if expected=='20261114_29':check_category_season_schema(conn,schema,inspector)


def migrate(engine, target, metadata=None):
    if target not in MIGRATION_SOURCES:
        raise StartupBlocked('Only reviewed migration targets 20261022_24, 20261030_25 20261109_26 20261111_27 20261112_28 and 20261114_29 are supported')
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
    if args.expected_revision!=DEFAULT_REVISION and not (args.rollback or args.check_only or args.migrate):raise StartupBlocked('Older revision requires explicit rollback/check mode')
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



CATEGORY_SEASON_CONTRACT = {'category_season_categories': {'checks': [],
                                'columns': {'id': ('INTEGER', False),
                                            'key': ('TEXT', False),
                                            'name': ('TEXT', False),
                                            'parent': ('TEXT', False),
                                            'parent_key': ('TEXT', False),
                                            'snapshot_id': ('VARCHAR(36)', False)},
                                'fk': [(['snapshot_id'], 'category_season_snapshots', ['id'])],
                                'indexes': {},
                                'pk': ['snapshot_id', 'id'],
                                'unique': []},
 'category_season_clients': {'checks': [],
                             'columns': {'id': ('INTEGER', False),
                                         'name': ('VARCHAR(512)', False),
                                         'snapshot_id': ('VARCHAR(36)', False)},
                             'fk': [(['snapshot_id'], 'category_season_snapshots', ['id'])],
                             'indexes': {},
                             'pk': ['snapshot_id', 'id'],
                             'unique': [['snapshot_id', 'name']]},
 'category_season_coverage': {'checks': ['ck_cs_coverage_days', 'ck_cs_coverage_method'],
                              'columns': {'calendar_days': ('INTEGER', False),
                                          'method': ('VARCHAR(24)', False),
                                          'month': ('INTEGER', False),
                                          'recorded_days': ('INTEGER', False),
                                          'snapshot_id': ('VARCHAR(36)', False),
                                          'store': ('VARCHAR(512)', False),
                                          'year': ('INTEGER', False)},
                              'fk': [(['snapshot_id'], 'category_season_snapshots', ['id'])],
                              'indexes': {},
                              'pk': ['snapshot_id', 'store', 'year', 'month'],
                              'unique': []},
 'category_season_facts': {'checks': ['ck_cs_fact_month_lines'],
                           'columns': {'brand': ('TEXT', False),
                                       'category_id': ('INTEGER', False),
                                       'client_id': ('INTEGER', False),
                                       'id': ('BIGINT', False),
                                       'lines': ('BIGINT', False),
                                       'manufacturer': ('TEXT', False),
                                       'month': ('INTEGER', False),
                                       'revenue': ('NUMERIC', False),
                                       'snapshot_id': ('VARCHAR(36)', False),
                                       'store': ('VARCHAR(512)', False),
                                       'units': ('NUMERIC', False),
                                       'year': ('INTEGER', False)},
                           'fk': [(['snapshot_id', 'client_id'],
                                   'category_season_clients',
                                   ['snapshot_id', 'id']),
                                  (['snapshot_id', 'category_id'],
                                   'category_season_categories',
                                   ['snapshot_id', 'id'])],
                           'indexes': {'ix_category_season_fact_category': ['snapshot_id', 'category_id'],
                                       'ix_category_season_fact_scope': ['snapshot_id',
                                                                         'store',
                                                                         'year',
                                                                         'month']},
                           'pk': ['id'],
                           'unique': []},
 'category_season_memberships': {'checks': ['ck_cs_membership_kind'],
                                 'columns': {'client_id': ('INTEGER', False),
                                             'kind': ('VARCHAR(16)', False),
                                             'snapshot_id': ('VARCHAR(36)', False),
                                             'value': ('VARCHAR(255)', False)},
                                 'fk': [(['snapshot_id', 'client_id'],
                                         'category_season_clients',
                                         ['snapshot_id', 'id'])],
                                 'indexes': {'ix_category_season_membership_filter': ['snapshot_id',
                                                                                      'kind',
                                                                                      'value',
                                                                                      'client_id']},
                                 'pk': ['snapshot_id', 'client_id', 'kind', 'value'],
                                 'unique': []},
 'category_season_snapshots': {'checks': ['ck_cs_snapshot_status'],
                               'columns': {'duration_ms': ('BIGINT', True),
                                           'error_code': ('VARCHAR(64)', True),
                                           'fact_rows': ('BIGINT', False),
                                           'id': ('VARCHAR(36)', False),
                                           'options': ('JSON', False),
                                           'published_at': ('TIMESTAMPWITHTIMEZONE', True),
                                           'started_at': ('TIMESTAMPWITHTIMEZONE', False),
                                           'status': ('VARCHAR(16)', False),
                                           'through': ('DATE', False),
                                           'week': ('DATE', True)},
                               'fk': [],
                               'indexes': {},
                               'pk': ['id'],
                               'unique': [['week']]},
 'category_season_state': {'checks': ['ck_cs_state_singleton'],
                           'columns': {'active_id': ('VARCHAR(36)', True),
                                       'id': ('INTEGER', False),
                                       'next_due': ('TIMESTAMPWITHTIMEZONE', True)},
                           'fk': [(['active_id'], 'category_season_snapshots', ['id'])],
                           'indexes': {},
                           'pk': ['id'],
                           'unique': []}}

def check_category_season_schema(conn,schema,inspector):
    for table,spec in CATEGORY_SEASON_CONTRACT.items():
        if not inspector.has_table(table,schema=schema):raise StartupBlocked('Missing category snapshot table: '+table)
        columns={c['name']:c for c in inspector.get_columns(table,schema=schema)}
        for name,(typ,nullable) in spec['columns'].items():
            actual=columns.get(name)
            if not actual or type_name(actual['type'],conn.dialect)!=typ or actual['nullable']!=nullable:raise StartupBlocked('Snapshot column contract mismatch: '+table+'.'+name)
        if inspector.get_pk_constraint(table,schema=schema)['constrained_columns']!=spec['pk']:raise StartupBlocked('Snapshot PK mismatch: '+table)
        for names,target,cols in spec['fk']:
            if not any(f['constrained_columns']==names and f['referred_table']==target and f['referred_columns']==cols and f.get('referred_schema') in {None,schema} and f.get('options',{}).get('ondelete') == ('RESTRICT' if table=='category_season_state' else 'CASCADE') and f.get('options',{}).get('onupdate') in {None,'NO ACTION'} for f in inspector.get_foreign_keys(table,schema=schema)):raise StartupBlocked('Snapshot FK mismatch: '+table)
        unique={tuple(c['column_names']) for c in inspector.get_unique_constraints(table,schema=schema)}
        if any(tuple(c) not in unique for c in spec['unique']):raise StartupBlocked('Snapshot unique mismatch: '+table)
        indexes={i['name']:i for i in inspector.get_indexes(table,schema=schema)}
        for name,cols in spec['indexes'].items():
            if name not in indexes or indexes[name]['column_names']!=cols:raise StartupBlocked('Snapshot index mismatch: '+table)
        checks={c['name'] for c in inspector.get_check_constraints(table,schema=schema)}
        if not set(spec['checks'])<=checks:raise StartupBlocked('Snapshot check constraint mismatch: '+table)

    import re
    expected_checks={
      'ck_cs_snapshot_status':'statusinbuildingpublishedfailed',
      'ck_cs_state_singleton':'id=1',
      'ck_cs_membership_kind':'kindinmanagerbuyer_type',
      'ck_cs_fact_month_lines':'month>=1andmonth<=12andlines>0',
      'ck_cs_coverage_method':'methodinconfirmedestimatedexcluded',
      'ck_cs_coverage_days':'month>=1andmonth<=12andrecorded_days>=0andrecorded_days<=31',
    }
    for table,spec in CATEGORY_SEASON_CONTRACT.items():
        for check in inspector.get_check_constraints(table,schema=schema):
            if check['name'] in expected_checks:
                value=re.sub(r'::(?:character varying|text|integer|bigint)(?:\[\])?','',check['sqltext'].lower())
                value=re.sub(r'[^a-z0-9_><=]','',value).replace('=anyarray','in')
                if value!=expected_checks[check['name']]:raise StartupBlocked('Snapshot check definition mismatch: '+check['name'])
    if conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relname LIKE 'category_season_%' AND (NOT i.indisvalid OR NOT i.indisready OR i.indpred IS NOT NULL OR i.indexprs IS NOT NULL))"),{'schema':schema}):raise StartupBlocked('Snapshot indexes must be ready, valid and unconditional')
    if conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=:schema AND t.relname LIKE 'category_season_%' AND (NOT c.convalidated OR c.condeferrable OR c.condeferred))"),{'schema':schema}):raise StartupBlocked('Snapshot constraints must be validated and immediate')

if __name__ == '__main__':
    try:
        main()
    except (StartupBlocked, sa.exc.SQLAlchemyError, subprocess.CalledProcessError) as error:
        message = str(error) if isinstance(error, StartupBlocked) else type(error).__name__
        print('STARTUP_BLOCKED: '+message, file=sys.stderr)
        sys.exit(1)
