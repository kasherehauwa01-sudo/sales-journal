"""add import logs"""
from alembic import op
import sqlalchemy as sa

revision = "20260914_02"
down_revision = "20260914_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_batches", sa.Column("log_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("import_batches", "log_text")
