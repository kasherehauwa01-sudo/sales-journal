"""Bounded per-store monthly aggregates; no scan or automatic job in migration."""

from alembic import op
import sqlalchemy as sa

revision = "20261013_18"
down_revision = "20261012_17"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "seasonality_store_months",
        sa.Column("generation", sa.Integer(), primary_key=True),
        sa.Column("identity", sa.String(64), primary_key=True),
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Integer(), primary_key=True),
        sa.Column("store", sa.Text(), primary_key=True),
        sa.Column("incomplete_source", sa.Boolean(), nullable=False),
        sa.Column("units", sa.Numeric(30, 3), nullable=False),
        sa.Column("positive_lines", sa.BigInteger(), nullable=False),
        sa.Column("first_sale", sa.Date(), nullable=False),
        sa.Column("last_sale", sa.Date(), nullable=False),
    )


def downgrade():
    op.drop_table("seasonality_store_months")
