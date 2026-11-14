"""Explicit empty snapshot tables only; no sales scan, fill or scheduler activation."""

from alembic import op

revision = "20261114_29"
down_revision = "20261112_28"
branch_labels = depends_on = None
DDL = [
    "\n"
    "CREATE TABLE category_season_snapshots (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\tstatus VARCHAR(16) NOT NULL, \n"
    "\tweek DATE, \n"
    "\tstarted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tpublished_at TIMESTAMP WITH TIME ZONE, \n"
    "\tthrough DATE NOT NULL, \n"
    "\toptions JSON NOT NULL, \n"
    "\tfact_rows BIGINT NOT NULL, \n"
    "\tduration_ms BIGINT, \n"
    "\terror_code VARCHAR(64), \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_cs_snapshot_status CHECK (status IN ('building','published','failed')), \n"
    "\tUNIQUE (week)\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE category_season_categories (\n"
    "\tsnapshot_id VARCHAR(36) NOT NULL, \n"
    "\tid INTEGER NOT NULL, \n"
    "\tkey TEXT NOT NULL, \n"
    "\tparent_key TEXT NOT NULL, \n"
    "\tparent TEXT NOT NULL, \n"
    "\tname TEXT NOT NULL, \n"
    "\tPRIMARY KEY (snapshot_id, id), \n"
    "\tFOREIGN KEY(snapshot_id) REFERENCES category_season_snapshots (id) ON DELETE CASCADE\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE category_season_clients (\n"
    "\tsnapshot_id VARCHAR(36) NOT NULL, \n"
    "\tid INTEGER NOT NULL, \n"
    "\tname VARCHAR(512) NOT NULL, \n"
    "\tPRIMARY KEY (snapshot_id, id), \n"
    "\tUNIQUE (snapshot_id, name), \n"
    "\tFOREIGN KEY(snapshot_id) REFERENCES category_season_snapshots (id) ON DELETE CASCADE\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE category_season_coverage (\n"
    "\tsnapshot_id VARCHAR(36) NOT NULL, \n"
    "\tstore VARCHAR(512) NOT NULL, \n"
    "\tyear INTEGER NOT NULL, \n"
    "\tmonth INTEGER NOT NULL, \n"
    "\tmethod VARCHAR(24) NOT NULL, \n"
    "\trecorded_days INTEGER NOT NULL, \n"
    "\tcalendar_days INTEGER NOT NULL, \n"
    "\tPRIMARY KEY (snapshot_id, store, year, month), \n"
    "\tCONSTRAINT ck_cs_coverage_method CHECK (method IN ('confirmed','estimated','excluded')), \n"
    "\tCONSTRAINT ck_cs_coverage_days CHECK (month BETWEEN 1 AND 12 AND recorded_days BETWEEN 0 AND 31), \n"
    "\tFOREIGN KEY(snapshot_id) REFERENCES category_season_snapshots (id) ON DELETE CASCADE\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE category_season_state (\n"
    "\tid SERIAL NOT NULL, \n"
    "\tactive_id VARCHAR(36), \n"
    "\tnext_due TIMESTAMP WITH TIME ZONE, \n"
    "\tPRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_cs_state_singleton CHECK (id=1), \n"
    "\tFOREIGN KEY(active_id) REFERENCES category_season_snapshots (id) ON DELETE RESTRICT\n"
    ")\n"
    "\n",
    "\n"
    "CREATE TABLE category_season_facts (\n"
    "\tid BIGSERIAL NOT NULL, \n"
    "\tsnapshot_id VARCHAR(36) NOT NULL, \n"
    "\tcategory_id INTEGER NOT NULL, \n"
    "\tclient_id INTEGER NOT NULL, \n"
    "\tstore VARCHAR(512) NOT NULL, \n"
    "\tbrand TEXT NOT NULL, \n"
    "\tmanufacturer TEXT NOT NULL, \n"
    "\tyear INTEGER NOT NULL, \n"
    "\tmonth INTEGER NOT NULL, \n"
    "\tunits NUMERIC NOT NULL, \n"
    "\trevenue NUMERIC NOT NULL, \n"
    "\tlines BIGINT NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tFOREIGN KEY(snapshot_id, category_id) REFERENCES category_season_categories (snapshot_id, id) ON DELETE "
    "CASCADE, \n"
    "\tFOREIGN KEY(snapshot_id, client_id) REFERENCES category_season_clients (snapshot_id, id) ON DELETE "
    "CASCADE, \n"
    "\tCONSTRAINT ck_cs_fact_month_lines CHECK (month BETWEEN 1 AND 12 AND lines>0)\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_category_season_fact_category ON category_season_facts (snapshot_id, category_id)",
    "CREATE INDEX ix_category_season_fact_scope ON category_season_facts (snapshot_id, store, year, month)",
    "\n"
    "CREATE TABLE category_season_memberships (\n"
    "\tsnapshot_id VARCHAR(36) NOT NULL, \n"
    "\tclient_id INTEGER NOT NULL, \n"
    "\tkind VARCHAR(16) NOT NULL, \n"
    "\tvalue VARCHAR(255) NOT NULL, \n"
    "\tPRIMARY KEY (snapshot_id, client_id, kind, value), \n"
    "\tFOREIGN KEY(snapshot_id, client_id) REFERENCES category_season_clients (snapshot_id, id) ON DELETE "
    "CASCADE, \n"
    "\tCONSTRAINT ck_cs_membership_kind CHECK (kind IN ('manager','buyer_type'))\n"
    ")\n"
    "\n",
    "CREATE INDEX ix_category_season_membership_filter ON category_season_memberships (snapshot_id, kind, "
    "value, client_id)",
]


def upgrade():
    for sql in DDL:
        op.execute(sql)
    op.execute("INSERT INTO category_season_state(id) VALUES(1)")


def downgrade():
    raise RuntimeError(
        "No automatic downgrade: retain snapshots and use checked bridge launcher29"
    )
