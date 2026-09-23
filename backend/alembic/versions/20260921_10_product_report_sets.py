from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision="20260921_10";down_revision="20260921_09";branch_labels=None;depends_on=None

def upgrade():
 op.create_table("product_report_sets",sa.Column("id",sa.BigInteger(),primary_key=True),sa.Column("name",sa.String(255),nullable=False,unique=True),sa.Column("products",postgresql.JSONB(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now()),sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.func.now(),onupdate=sa.func.now()))

def downgrade():
 op.drop_table("product_report_sets")
