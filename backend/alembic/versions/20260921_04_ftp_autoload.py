"""FTP autoload settings and history."""
from alembic import op
import sqlalchemy as sa

revision="20260921_04";down_revision="20260915_03";branch_labels=None;depends_on=None

def upgrade():
 op.create_table("ftp_config",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("protocol",sa.String(16),nullable=False),sa.Column("host",sa.String(255),nullable=False),sa.Column("port",sa.Integer(),nullable=False),sa.Column("username",sa.String(255),nullable=False),sa.Column("password",sa.Text(),nullable=False),sa.Column("directory",sa.String(1024),nullable=False),sa.Column("retries",sa.Integer(),nullable=False),sa.Column("retry_delay",sa.Integer(),nullable=False),sa.Column("enabled",sa.Boolean(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))
 op.create_table("auto_import_logs",sa.Column("id",sa.BigInteger(),primary_key=True),sa.Column("filename",sa.String(512)),sa.Column("status",sa.String(32),nullable=False),sa.Column("message",sa.Text()),sa.Column("import_id",sa.BigInteger(),sa.ForeignKey("import_batches.id",ondelete="SET NULL")),sa.Column("started_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),sa.Column("finished_at",sa.DateTime(timezone=True)))
 op.create_index("ix_auto_import_logs_status","auto_import_logs",["status"]);op.create_index("ix_auto_import_logs_import_id","auto_import_logs",["import_id"])

def downgrade():
 op.drop_table("auto_import_logs");op.drop_table("ftp_config")
