from alembic import op
import sqlalchemy as sa

revision="20260921_09";down_revision="20260921_08";branch_labels=None;depends_on=None

def upgrade():
 op.alter_column("scenarios","email",existing_type=sa.String(255),type_=sa.Text(),existing_nullable=False)

def downgrade():
 op.alter_column("scenarios","email",existing_type=sa.Text(),type_=sa.String(255),existing_nullable=False)
