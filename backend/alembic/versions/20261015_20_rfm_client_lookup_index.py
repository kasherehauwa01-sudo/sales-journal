"""Client-scoped lookup: EXPLAIN benchmark confirms normalized-key index use.

Concurrent build avoids blocking sales writes on the existing production table.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261015_20"
down_revision = "20261015_19"
branch_labels = None
depends_on = None
NAME = "ix_sales_rfm_normalized_client_date"


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(NAME, "sales", [sa.text("lower(trim(client))"), "sale_date"],
                        postgresql_concurrently=True)


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(NAME, table_name="sales", postgresql_concurrently=True)
