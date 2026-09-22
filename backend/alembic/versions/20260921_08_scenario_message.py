from alembic import op
import sqlalchemy as sa
revision="20260921_08";down_revision="20260921_07";branch_labels=None;depends_on=None
def upgrade():
 op.add_column("scenarios",sa.Column("message_text",sa.Text(),nullable=False,server_default="Направляем отчет по продажам HoReCa за указанный период."))
def downgrade():
 op.drop_column("scenarios","message_text")
