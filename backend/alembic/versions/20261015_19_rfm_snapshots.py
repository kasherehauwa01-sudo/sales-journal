"""Persistent RFM generations; no source data scan or rebuild during migration."""
from alembic import op
import sqlalchemy as sa

revision = "20261015_19"
down_revision = "20261013_18"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("rfm_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_count", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("algorithm_version", sa.Integer(), nullable=False),
        sa.Column("boundaries", sa.JSON(), nullable=False))
    op.create_table("rfm_snapshot_clients",
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("rfm_snapshots.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("client_key", sa.String(512), primary_key=True),
        sa.Column("client", sa.String(512), nullable=False),
        *[sa.Column(name, sa.Integer(), nullable=False) for name in ("r_score", "f_score", "m_score", "recency_days", "frequency")],
        sa.Column("rfm_code", sa.String(3), nullable=False),
        sa.Column("segment", sa.String(64), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("monetary", sa.Numeric(30, 2), nullable=False),
        sa.Column("average_check", sa.Numeric(30, 6), nullable=False),
        sa.Column("first_purchase", sa.Date(), nullable=False),
        sa.Column("last_purchase", sa.Date(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False))
    op.create_table("rfm_snapshot_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("active_snapshot_id", sa.String(36), sa.ForeignKey("rfm_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_rfm_singleton"))


def downgrade():
    op.drop_table("rfm_snapshot_state")
    op.drop_table("rfm_snapshot_clients")
    op.drop_table("rfm_snapshots")
