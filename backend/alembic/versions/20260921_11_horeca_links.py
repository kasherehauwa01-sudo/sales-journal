from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision="20260921_11";down_revision="20260921_10";branch_labels=None;depends_on=None

def upgrade():
 op.add_column("scenarios",sa.Column("reply_emails",sa.Text(),nullable=False,server_default=""))
 op.create_table("horeca_reports",sa.Column("id",sa.BigInteger(),primary_key=True),sa.Column("token",sa.String(64),nullable=False,unique=True),sa.Column("scenario_id",sa.BigInteger(),sa.ForeignKey("scenarios.id",ondelete="CASCADE"),nullable=False),sa.Column("period_start",sa.Date(),nullable=False),sa.Column("period_end",sa.Date(),nullable=False),sa.Column("products",postgresql.JSONB(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now()))
 op.create_index("ix_horeca_reports_token","horeca_reports",["token"],unique=True)

def downgrade():
 op.drop_index("ix_horeca_reports_token",table_name="horeca_reports");op.drop_table("horeca_reports");op.drop_column("scenarios","reply_emails")
