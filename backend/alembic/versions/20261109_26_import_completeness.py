"""Import completeness metadata; disabled by default; no historical sales scan."""

from alembic import op

revision = "20261109_26"
down_revision = "20261030_25"
branch_labels = depends_on = None
DDL = [
    r"""CREATE TABLE import_control_audit (
	id BIGSERIAL NOT NULL,
	entity VARCHAR(32) NOT NULL,
	entity_id VARCHAR(64) NOT NULL,
	actor VARCHAR(255) NOT NULL,
	effective_date DATE,
	changes JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
)""",
    r"""CREATE TABLE import_control_config (
	id SERIAL NOT NULL,
	enabled BOOLEAN DEFAULT 'false' NOT NULL,
	automatic BOOLEAN DEFAULT 'false' NOT NULL,
	email_enabled BOOLEAN DEFAULT 'false' NOT NULL,
	start_date DATE,
	day_offset INTEGER,
	marker_template VARCHAR(512),
	recipients TEXT DEFAULT '' NOT NULL,
	revision INTEGER DEFAULT '1' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (id=1),
	CHECK (day_offset IS NULL OR day_offset BETWEEN 0 AND 7)
)""",
    r"""CREATE TABLE import_control_cycles (
	id VARCHAR(36) NOT NULL,
	sales_date DATE,
	status VARCHAR(24) NOT NULL,
	source_confirmed BOOLEAN DEFAULT 'false' NOT NULL,
	config_revision INTEGER,
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	error_code VARCHAR(64),
	PRIMARY KEY (id)
)""",
    r"""CREATE TABLE import_control_notices (
	id BIGSERIAL NOT NULL,
	sales_date DATE NOT NULL,
	dedupe_key VARCHAR(64) NOT NULL,
	status VARCHAR(24) NOT NULL,
	attempts INTEGER DEFAULT '0' NOT NULL,
	recipients TEXT NOT NULL,
	subject VARCHAR(255) NOT NULL,
	body TEXT NOT NULL,
	error_code VARCHAR(64),
	next_attempt TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	sent_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	UNIQUE (dedupe_key)
)""",
    r"""CREATE TABLE import_day_checks (
	sales_date DATE NOT NULL,
	status VARCHAR(32) NOT NULL,
	expected INTEGER NOT NULL,
	successful INTEGER NOT NULL,
	missing INTEGER NOT NULL,
	errors INTEGER NOT NULL,
	checked_at TIMESTAMP WITH TIME ZONE NOT NULL,
	config_revision INTEGER NOT NULL,
	error_code VARCHAR(64),
	PRIMARY KEY (sales_date)
)""",
    r"""CREATE TABLE import_departments (
	id BIGSERIAL NOT NULL,
	code VARCHAR(128) NOT NULL,
	name VARCHAR(255) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (code)
)""",
    r"""CREATE TABLE import_control_receipts (
	id BIGSERIAL NOT NULL,
	import_id BIGINT NOT NULL,
	cycle_id VARCHAR(36),
	filename VARCHAR(512) NOT NULL,
	sha256 VARCHAR(64),
	status VARCHAR(32) NOT NULL,
	period_start DATE,
	period_end DATE,
	period_method VARCHAR(64),
	departments JSON NOT NULL,
	error_code VARCHAR(64),
	duplicate_of BIGINT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	UNIQUE (import_id),
	FOREIGN KEY(import_id) REFERENCES import_batches (id) ON DELETE RESTRICT,
	FOREIGN KEY(cycle_id) REFERENCES import_control_cycles (id) ON DELETE RESTRICT,
	FOREIGN KEY(duplicate_of) REFERENCES import_control_receipts (id) ON DELETE RESTRICT
)""",
    r"""CREATE TABLE import_expectation_rules (
	id BIGSERIAL NOT NULL,
	department_id BIGINT NOT NULL,
	start_date DATE NOT NULL,
	end_date DATE,
	filename_template VARCHAR(512) NOT NULL,
	date_format VARCHAR(8) DEFAULT 'dmy' NOT NULL,
	display_name VARCHAR(255) NOT NULL,
	active BOOLEAN DEFAULT 'true' NOT NULL,
	controlled BOOLEAN DEFAULT 'true' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (end_date IS NULL OR end_date>=start_date),
	CHECK (date_format IN ('dmy','iso')),
	FOREIGN KEY(department_id) REFERENCES import_departments (id) ON DELETE RESTRICT
)""",
    r"""CREATE TABLE import_control_issues (
	id BIGSERIAL NOT NULL,
	sales_date DATE NOT NULL,
	department_id BIGINT NOT NULL,
	rule_id BIGINT NOT NULL,
	expected_filename VARCHAR(512) NOT NULL,
	department_name VARCHAR(255) NOT NULL,
	status VARCHAR(24) NOT NULL,
	error_code VARCHAR(64),
	import_id BIGINT,
	detected_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	checked_at TIMESTAMP WITH TIME ZONE NOT NULL,
	resolved_at TIMESTAMP WITH TIME ZONE,
	closure_reason TEXT,
	closure_method VARCHAR(32),
	PRIMARY KEY (id),
	CONSTRAINT uq_import_issue_day_department UNIQUE (sales_date, department_id),
	FOREIGN KEY(department_id) REFERENCES import_departments (id) ON DELETE RESTRICT,
	FOREIGN KEY(rule_id) REFERENCES import_expectation_rules (id) ON DELETE RESTRICT,
	FOREIGN KEY(import_id) REFERENCES import_batches (id) ON DELETE RESTRICT
)""",
    r"""CREATE INDEX ix_import_control_audit_entity ON import_control_audit (entity)""",
    r"""CREATE INDEX ix_import_control_cycles_sales_date ON import_control_cycles (sales_date)""",
    r"""CREATE INDEX ix_import_control_cycles_status ON import_control_cycles (status)""",
    r"""CREATE INDEX ix_import_control_notices_status ON import_control_notices (status)""",
    r"""CREATE INDEX ix_import_control_notices_sales_date ON import_control_notices (sales_date)""",
    r"""CREATE INDEX ix_import_control_notices_next_attempt ON import_control_notices (next_attempt)""",
    r"""CREATE INDEX ix_import_control_receipts_cycle_id ON import_control_receipts (cycle_id)""",
    r"""CREATE INDEX ix_import_control_receipts_filename ON import_control_receipts (filename)""",
    r"""CREATE INDEX ix_import_control_receipts_period_start ON import_control_receipts (period_start)""",
    r"""CREATE INDEX ix_import_control_receipts_status ON import_control_receipts (status)""",
    r"""CREATE INDEX ix_import_expectation_rules_start_date ON import_expectation_rules (start_date)""",
    r"""CREATE INDEX ix_import_expectation_rules_department_id ON import_expectation_rules (department_id)""",
    r"""CREATE INDEX ix_import_control_issues_sales_date ON import_control_issues (sales_date)""",
    r"""CREATE INDEX ix_import_control_issues_status ON import_control_issues (status)""",
    r"""CREATE INDEX ix_import_control_issues_department_id ON import_control_issues (department_id)""",
]


def upgrade():
    for sql in DDL:
        op.execute(sql)
    op.execute(RULE_GUARD)


def downgrade():
    raise RuntimeError(
        "Destructive downgrade disabled: preserve control/audit history; use checked old-image launcher rollback"
    )


RULE_GUARD = r"""
CREATE FUNCTION import_filename_mask(value text, fmt text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT replace(value,'{date}',CASE WHEN fmt='dmy' THEN '##.##.####' ELSE '####-##-##' END)
$$;
CREATE FUNCTION import_masks_overlap(a text,b text) RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE i integer; x text; y text;
BEGIN
 IF length(a)<>length(b) THEN RETURN false; END IF;
 FOR i IN 1..length(a) LOOP
  x=substr(a,i,1);y=substr(b,i,1);
  IF NOT (x=y OR (x='#' AND y~'^[0-9]$') OR (y='#' AND x~'^[0-9]$')) THEN RETURN false;END IF;
 END LOOP;
 RETURN true;
END $$;
CREATE FUNCTION import_rule_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(734190270);
 IF NEW.filename_template LIKE '%#%' OR NEW.filename_template LIKE '%\\%' OR NEW.filename_template LIKE '%/%' THEN
  RAISE EXCEPTION 'invalid_import_template' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM import_expectation_rules candidate WHERE candidate.id<>NEW.id
  AND daterange(candidate.start_date,candidate.end_date,'[]') && daterange(NEW.start_date,NEW.end_date,'[]')
  AND (candidate.department_id=NEW.department_id OR (candidate.active AND candidate.controlled AND NEW.active AND NEW.controlled
   AND import_masks_overlap(import_filename_mask(candidate.filename_template,candidate.date_format),import_filename_mask(NEW.filename_template,NEW.date_format))))) THEN
  RAISE EXCEPTION 'overlapping_import_rules' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER import_rule_guard BEFORE INSERT OR UPDATE ON import_expectation_rules
 FOR EACH ROW EXECUTE FUNCTION import_rule_guard();
"""
