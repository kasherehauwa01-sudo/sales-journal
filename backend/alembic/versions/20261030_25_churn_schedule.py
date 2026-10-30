"""Opt-in permanent churn schedule and receipts; no existing rows changed."""
from alembic import op
import sqlalchemy as sa

revision = '20261030_25'
down_revision = '20261022_24'
branch_labels = depends_on = None


def upgrade():
    op.create_table('churn_schedule', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False), sa.Column('approved', sa.Boolean(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False), sa.Column('settings_json', sa.JSON(), nullable=False))
    op.create_table('churn_jobs', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('business_date', sa.Date(), nullable=False), sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('policy_revision', sa.Integer(), nullable=False), sa.Column('status', sa.String(32), nullable=False),
        sa.Column('run_id', sa.String(36)), sa.Column('release_id', sa.String(36), sa.ForeignKey('analytics_automation_releases.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('counters', sa.JSON(), nullable=False), sa.Column('error_code', sa.String(64)),
        sa.UniqueConstraint('business_date', 'kind', name='uq_churn_job_day_kind'))
    op.create_table('churn_receipts', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('job_id', sa.String(36), sa.ForeignKey('churn_jobs.id'), nullable=False),
        sa.Column('event_id', sa.BigInteger(), nullable=False), sa.Column('event_hash', sa.String(64), nullable=False),
        sa.Column('decision', sa.String(32), nullable=False), sa.Column('task_id', sa.String(64)),
        sa.Column('created_for_release', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('reasons', sa.JSON(), nullable=False), sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('job_id', 'event_id', name='uq_churn_receipt_event'))


def downgrade():
    for table in ('churn_receipts', 'churn_jobs', 'churn_schedule'):
        op.drop_table(table)
