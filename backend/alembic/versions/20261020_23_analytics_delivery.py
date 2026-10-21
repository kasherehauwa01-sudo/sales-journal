"""Transactional delivery publications. Apply with analytics worker stopped."""
from alembic import op
import sqlalchemy as sa
revision = "20261020_23"
down_revision = "20261020_22"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_analytics_change_run_id", "analytics_signal_changes", ["run_id", "id"])
    op.create_table("analytics_delivery_head", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("sequence", sa.BigInteger(), nullable=False))
    op.create_table("analytics_delivery_publications", sa.Column("sequence", sa.BigInteger(), primary_key=True, autoincrement=False),
                    sa.Column("run_id", sa.String(36), sa.ForeignKey("analytics_signal_runs.id"), nullable=False, unique=True))
    # Existing completed runs are a one-time frozen bootstrap, not live auto-DDL.
    op.execute("""INSERT INTO analytics_delivery_publications(sequence,run_id)
      SELECT row_number() OVER (ORDER BY finished_at,id),id FROM analytics_signal_runs WHERE status='SUCCEEDED'""")
    op.execute("INSERT INTO analytics_delivery_head(id,sequence) SELECT 1,COALESCE(MAX(sequence),0) FROM analytics_delivery_publications")


def downgrade():
    op.drop_index("ix_analytics_change_run_id", table_name="analytics_signal_changes")
    op.drop_table("analytics_delivery_publications")
    op.drop_table("analytics_delivery_head")
