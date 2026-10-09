"""Устройства mTLS и журнал административных действий."""
from alembic import op
import sqlalchemy as sa

revision="20261006_14";down_revision="20261002_13";branch_labels=None;depends_on=None

def upgrade():
 op.create_table("access_devices",
  sa.Column("id",sa.BigInteger(),primary_key=True),sa.Column("device_name",sa.String(255),nullable=False),
  sa.Column("owner",sa.String(255)),sa.Column("comment",sa.Text()),sa.Column("common_name",sa.String(255),nullable=False),
  sa.Column("serial_number",sa.String(255),nullable=False),sa.Column("fingerprint",sa.String(128),nullable=False),
  sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),sa.Column("status",sa.String(32),nullable=False),
  sa.Column("last_used_at",sa.DateTime(timezone=True)),sa.Column("revoked_at",sa.DateTime(timezone=True)),
  sa.Column("deleted_at",sa.DateTime(timezone=True)),sa.Column("package_available",sa.Boolean(),nullable=False,server_default=sa.false()),
  sa.Column("package_expires_at",sa.DateTime(timezone=True)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
  sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
  sa.CheckConstraint("status IN ('issuing','active','revoked','failed')",name="ck_access_devices_status"),
  sa.UniqueConstraint("common_name"),sa.UniqueConstraint("serial_number"),sa.UniqueConstraint("fingerprint"))
 for column in ("common_name","serial_number","fingerprint","status","last_used_at","deleted_at"):op.create_index(f"ix_access_devices_{column}","access_devices",[column])
 op.create_table("access_audit_events",sa.Column("id",sa.BigInteger(),primary_key=True),
  sa.Column("device_id",sa.BigInteger(),sa.ForeignKey("access_devices.id",ondelete="SET NULL")),
  sa.Column("action",sa.String(64),nullable=False),sa.Column("actor",sa.String(255),nullable=False),
  sa.Column("ip_address",sa.String(64)),sa.Column("details",sa.JSON()),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
 for column in ("device_id","action","created_at"):op.create_index(f"ix_access_audit_events_{column}","access_audit_events",[column])

def downgrade():
 op.drop_table("access_audit_events");op.drop_table("access_devices")
