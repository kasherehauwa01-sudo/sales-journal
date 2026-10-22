"""Separate immutable CHURN authorization stream; SHADOW history is untouched."""
from alembic import op
import sqlalchemy as sa
revision='20261022_24'
down_revision='20261020_23'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('analytics_automation_policy',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('enabled',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('approved',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('revision',sa.Integer(),nullable=False,server_default='1'),
        sa.Column('settings_json',sa.JSON(),nullable=False),sa.Column('sequence',sa.BigInteger(),nullable=False,server_default='0'))
    op.execute("INSERT INTO analytics_automation_policy(id,settings_json) VALUES (1,'{}')")
    op.create_table('analytics_automation_identities',sa.Column('subject_key',sa.String(64),primary_key=True),
        sa.Column('client_id',sa.String(255),nullable=False),sa.Column('manager_id',sa.BigInteger(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),sa.Column('status',sa.String(20),nullable=False,server_default='REVIEW'),
        sa.Column('evidence',sa.JSON(),nullable=False))
    op.create_table('analytics_automation_releases',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('sequence',sa.BigInteger(),nullable=False,unique=True),
        sa.Column('source_run_id',sa.String(36),sa.ForeignKey('analytics_signal_runs.id'),nullable=False,unique=True),
        sa.Column('metadata_json',sa.JSON(),nullable=False),sa.Column('proof_hash',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('analytics_automation_events',sa.Column('id',sa.BigInteger(),primary_key=True,autoincrement=True),
        sa.Column('release_id',sa.String(36),sa.ForeignKey('analytics_automation_releases.id'),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False))
    op.create_index('ix_automation_release_event','analytics_automation_events',['release_id','id'])
    op.create_table('analytics_automation_decisions',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('source_run_id',sa.String(36),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('data',sa.JSON(),nullable=False))

def downgrade():
    for name in ('analytics_automation_decisions','analytics_automation_events','analytics_automation_releases','analytics_automation_identities','analytics_automation_policy'):
        op.drop_table(name)
