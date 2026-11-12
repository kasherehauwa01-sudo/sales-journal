"""Explicit small metadata only. Required sales index is prepared separately."""

from alembic import op
import sqlalchemy as sa

revision = "20261112_28"
down_revision = "20261111_27"
branch_labels = depends_on = None
DDL = [
    "CREATE TABLE sales_control_settings (\n\tid SERIAL NOT NULL, \n\tenabled BOOLEAN DEFAULT 'false' NOT NULL, \n\tsend_time VARCHAR(5) DEFAULT '09:00' NOT NULL, \n\trecipients JSON DEFAULT '[]' NOT NULL, \n\tnot_before TIMESTAMP WITH TIME ZONE, \n\trevision INTEGER DEFAULT '1' NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_sales_control_singleton CHECK (id=1), \n\tCONSTRAINT ck_sales_control_time CHECK (send_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$')\n)",
    "CREATE TABLE sales_control_exclusions (\n\tdepartment VARCHAR(512) NOT NULL, \n\tsales_date DATE NOT NULL, \n\thidden_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tactor VARCHAR(255) NOT NULL, \n\tPRIMARY KEY (department, sales_date)\n)",
    "CREATE INDEX ix_sales_control_exclusions_date ON sales_control_exclusions (sales_date)",
    "CREATE TABLE sales_control_audit (\n\tid SERIAL NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tactor VARCHAR(255) NOT NULL, \n\taction VARCHAR(32) NOT NULL, \n\tdetails JSON NOT NULL, \n\tPRIMARY KEY (id)\n)",
    "CREATE TABLE sales_control_deliveries (\n\tid SERIAL NOT NULL, \n\tsettings_id INTEGER DEFAULT '1' NOT NULL, \n\tsales_date DATE NOT NULL, \n\trecipients_hash VARCHAR(64) NOT NULL, \n\tchecked_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tmissing_count INTEGER DEFAULT '0' NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tattempted_at TIMESTAMP WITH TIME ZONE, \n\tsent_at TIMESTAMP WITH TIME ZONE, \n\tnext_attempt TIMESTAMP WITH TIME ZONE, \n\tattempts INTEGER DEFAULT '0' NOT NULL, \n\terror_code VARCHAR(48), \n\tmanual_retry BOOLEAN DEFAULT 'false' NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_sales_control_delivery UNIQUE (sales_date, recipients_hash), \n\tCONSTRAINT ck_sales_control_delivery_status CHECK (status IN ('sending','sent','failed','uncertain','not_required')), \n\tCONSTRAINT ck_sales_control_counts CHECK (attempts>=0 AND missing_count>=0), \n\tFOREIGN KEY(settings_id) REFERENCES sales_control_settings (id) ON DELETE RESTRICT\n)",
    "CREATE INDEX ix_sales_control_delivery_checked ON sales_control_deliveries (checked_at)",
]


def upgrade():

    # Synchronous equivalent; do not create a large index in migration/startup.
    conn = op.get_bind()
    valid = conn.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname='ix_sales_department_date_control' AND i.indrelid='sales'::regclass AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=2 AND pg_get_indexdef(i.indexrelid,1,true)='department' AND pg_get_indexdef(i.indexrelid,2,true)='sale_date')"
        )
    )
    if not valid:
        raise RuntimeError("Prepare reviewed concurrent sales index before migration28")
    for statement in DDL:
        op.execute(statement)
    op.execute("INSERT INTO sales_control_settings(id) VALUES(1)")


def downgrade():
    raise RuntimeError(
        "No automatic downgrade: retain new metadata and use checked bridge rollback"
    )
