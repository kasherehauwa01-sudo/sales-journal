"""Explicit historical reconciliation metadata; no sales scan or default job."""

from alembic import op

revision = "20261111_27"
down_revision = "20261109_26"
branch_labels = depends_on = None
DDL = [
    """CREATE TABLE import_history_job (
	id SERIAL NOT NULL,
	status VARCHAR(24) DEFAULT 'paused' NOT NULL,
	plan JSON NOT NULL,
	rules_hash VARCHAR(64) NOT NULL,
	position INTEGER DEFAULT '0' NOT NULL,
	next_date DATE,
	checked INTEGER DEFAULT '0' NOT NULL,
	total INTEGER NOT NULL,
	error_code VARCHAR(64),
	started_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (id=1),
	CHECK (status IN ('running','paused','completed','error')),
	CHECK (position>=0 AND checked>=0 AND total>=checked)
)""",
    """CREATE TABLE import_history_results (
	sales_date DATE NOT NULL,
	department_id BIGINT NOT NULL,
	rule_id BIGINT,
	expected_filename VARCHAR(512),
	status VARCHAR(24) NOT NULL,
	error_code VARCHAR(64),
	checked_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (sales_date, department_id),
	CHECK (status IN ('success','missing','error','unavailable','waiting','not_expected')),
	FOREIGN KEY(department_id) REFERENCES import_departments (id) ON DELETE RESTRICT,
	FOREIGN KEY(rule_id) REFERENCES import_expectation_rules (id) ON DELETE RESTRICT
)""",
    """CREATE INDEX ix_import_history_results_expected_filename ON import_history_results (expected_filename)""",
    """CREATE INDEX ix_import_history_results_department_date ON import_history_results (department_id, sales_date)""",
    """CREATE INDEX ix_import_history_results_status ON import_history_results (status)""",
]


def upgrade():
    for statement in DDL:
        op.execute(statement)


def downgrade():
    raise RuntimeError(
        "No destructive downgrade: retain reconciliation history and use a reviewed maintenance bridge"
    )
