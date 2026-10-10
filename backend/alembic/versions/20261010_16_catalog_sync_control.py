"""Operator-controlled worker; existing attributes/checkpoints are untouched."""

from alembic import op
import sqlalchemy as sa

revision = "20261010_16"
down_revision = "20261010_15"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "catalog_sync_control",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("requested", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        *[
            sa.Column(name, sa.DateTime(timezone=True))
            for name in ("heartbeat_at", "run_started_at", "cycle_started_at")
        ],
        sa.Column("tracked_cycle", sa.BigInteger()),
        sa.Column("scanned", sa.BigInteger(), nullable=False),
        sa.Column("processed", sa.BigInteger(), nullable=False),
        sa.Column("error_code", sa.String(32)),
        sa.Column("refresh_cursor", sa.String(64)),
        sa.Column("refresh_upper", sa.String(64)),
        sa.Column("refresh_completed", sa.Boolean(), nullable=False),
    )
    op.execute(
        "INSERT INTO catalog_sync_control (id,requested,status,mode,scanned,processed,refresh_completed) VALUES (1,false,'paused','discovery',0,0,true)"
    )


def downgrade():
    op.drop_table("catalog_sync_control")
