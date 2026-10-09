"""Индексы для клиентских RFM-срезов.

Revision ID: 20261002_13
Revises: 20260921_12
"""
from alembic import op

revision = "20261002_13"
down_revision = "20260921_12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_sales_rfm_date_client", "sales", ["sale_date", "client"])
    op.create_index("ix_sales_rfm_client_date", "sales", ["client", "sale_date"])


def downgrade():
    op.drop_index("ix_sales_rfm_client_date", table_name="sales")
    op.drop_index("ix_sales_rfm_date_client", table_name="sales")
