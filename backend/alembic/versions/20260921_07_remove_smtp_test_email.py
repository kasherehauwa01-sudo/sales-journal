from alembic import op
import sqlalchemy as sa

revision="20260921_07";down_revision="20260921_06";branch_labels=None;depends_on=None

def upgrade():
 op.drop_column("smtp_config","test_email")

def downgrade():
 op.add_column("smtp_config",sa.Column("test_email",sa.String(255),nullable=True))
