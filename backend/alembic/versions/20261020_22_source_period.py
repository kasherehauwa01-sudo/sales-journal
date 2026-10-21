"""Nullable declared report period; existing observed dates are unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "20261020_22"
down_revision = "20261020_21"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("import_batches", sa.Column("declared_period_start", sa.Date(), nullable=True))
    op.add_column("import_batches", sa.Column("declared_period_end", sa.Date(), nullable=True))
    op.add_column("import_batches", sa.Column("source_period_method", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("import_batches", "source_period_method")
    op.drop_column("import_batches", "declared_period_end")
    op.drop_column("import_batches", "declared_period_start")
