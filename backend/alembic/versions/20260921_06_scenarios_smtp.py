from alembic import op
import sqlalchemy as sa
revision="20260921_06";down_revision="20260921_05";branch_labels=None;depends_on=None
def upgrade():
 op.create_table("smtp_config",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("host",sa.String(255),nullable=False),sa.Column("port",sa.Integer(),nullable=False),sa.Column("security",sa.String(16),nullable=False),sa.Column("username",sa.String(255),nullable=False),sa.Column("password",sa.Text(),nullable=False),sa.Column("sender_email",sa.String(255),nullable=False),sa.Column("sender_name",sa.String(255),nullable=False),sa.Column("test_email",sa.String(255)),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now()),sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.func.now()))
 op.create_table("scenarios",sa.Column("id",sa.BigInteger(),primary_key=True),sa.Column("name",sa.String(255),nullable=False),sa.Column("email",sa.String(255),nullable=False),sa.Column("manager",sa.String(255),nullable=False),sa.Column("enabled",sa.Boolean(),nullable=False,server_default=sa.true()),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now()),sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.func.now(),onupdate=sa.func.now()))
 op.create_table("scenario_runs",sa.Column("id",sa.BigInteger(),primary_key=True),sa.Column("scenario_id",sa.BigInteger(),sa.ForeignKey("scenarios.id",ondelete="CASCADE"),nullable=False),sa.Column("run_date",sa.Date(),nullable=False),sa.Column("status",sa.String(32),nullable=False),sa.Column("message",sa.Text()),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now()),sa.UniqueConstraint("scenario_id","run_date"))
 op.execute("INSERT INTO scenarios (name,email,manager,enabled) VALUES ('Продажи HoReCa','','Трошина Лариса',true)")
def downgrade():
 op.drop_table("scenario_runs");op.drop_table("scenarios");op.drop_table("smtp_config")
