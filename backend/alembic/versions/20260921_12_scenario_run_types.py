from alembic import op
import sqlalchemy as sa

revision="20260921_12";down_revision="20260921_11";branch_labels=None;depends_on=None

def upgrade():
 op.drop_constraint("scenario_runs_scenario_id_run_date_key","scenario_runs",type_="unique")
 op.add_column("scenario_runs",sa.Column("run_type",sa.String(32),nullable=False,server_default="scheduled"))
 op.add_column("scenario_runs",sa.Column("period_start",sa.Date(),nullable=True))
 op.add_column("scenario_runs",sa.Column("period_end",sa.Date(),nullable=True))
 op.add_column("scenario_runs",sa.Column("recipients",sa.Text(),nullable=True))
 op.create_index("uq_scenario_runs_scheduled_date","scenario_runs",["scenario_id","run_date"],unique=True,postgresql_where=sa.text("run_type = 'scheduled'"))

def downgrade():
 op.drop_index("uq_scenario_runs_scheduled_date",table_name="scenario_runs");op.drop_column("scenario_runs","recipients");op.drop_column("scenario_runs","period_end");op.drop_column("scenario_runs","period_start");op.drop_column("scenario_runs","run_type")
 op.create_unique_constraint("scenario_runs_scenario_id_run_date_key","scenario_runs",["scenario_id","run_date"])
