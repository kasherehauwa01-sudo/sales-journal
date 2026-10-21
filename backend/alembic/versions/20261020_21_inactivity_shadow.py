"""Shadow inactivity infrastructure; no source scan, no enabled worker."""
from alembic import op

revision = "20261020_21"
down_revision = "20261015_20"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE analytics_signal_runs (
    id VARCHAR(36) NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    phase VARCHAR(20) NOT NULL, 
    as_of DATE NOT NULL, 
    source_revision BIGINT NOT NULL, 
    upper_sale_id BIGINT NOT NULL, 
    sale_cursor BIGINT NOT NULL, 
    client_cursor VARCHAR(512) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    heartbeat_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    metadata_json JSON NOT NULL, 
    counters JSON NOT NULL, 
    error_count INTEGER NOT NULL, 
    error_code VARCHAR(64), 
    PRIMARY KEY (id)
)

""")
    op.execute('CREATE INDEX ix_analytics_signal_runs_status ON analytics_signal_runs (status)')
    op.execute("""
CREATE TABLE analytics_client_evaluations (
    run_id VARCHAR(36) NOT NULL, 
    client_key VARCHAR(512) NOT NULL, 
    display_name VARCHAR(512) NOT NULL, 
    eligible BOOLEAN NOT NULL, 
    reason VARCHAR(64) NOT NULL, 
    signal_type VARCHAR(32), 
    status VARCHAR(20) NOT NULL, 
    score NUMERIC(5, 2) NOT NULL, 
    confidence NUMERIC(5, 4) NOT NULL, 
    data JSON NOT NULL, 
    PRIMARY KEY (run_id, client_key), 
    FOREIGN KEY(run_id) REFERENCES analytics_signal_runs (id) ON DELETE CASCADE
)

""")
    op.execute('CREATE INDEX ix_analytics_evaluation_report ON analytics_client_evaluations (run_id, status, score, client_key)')
    op.execute("""
CREATE TABLE analytics_observed_phones (
    run_id VARCHAR(36) NOT NULL, 
    client_key VARCHAR(512) NOT NULL, 
    phone VARCHAR(64) NOT NULL, 
    last_seen DATE NOT NULL, 
    PRIMARY KEY (run_id, client_key, phone), 
    FOREIGN KEY(run_id) REFERENCES analytics_signal_runs (id) ON DELETE CASCADE
)

""")
    op.execute("""
CREATE TABLE analytics_purchase_days (
    run_id VARCHAR(36) NOT NULL, 
    client_key VARCHAR(512) NOT NULL, 
    sale_date DATE NOT NULL, 
    display_name VARCHAR(512) NOT NULL, 
    checks BIGINT NOT NULL, 
    positive_checks BIGINT NOT NULL, 
    revenue NUMERIC(30, 2) NOT NULL, 
    PRIMARY KEY (run_id, client_key, sale_date), 
    FOREIGN KEY(run_id) REFERENCES analytics_signal_runs (id) ON DELETE CASCADE
)

""")
    op.execute("""
CREATE TABLE analytics_signal_state (
    id INTEGER NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    active_run_id VARCHAR(36), 
    published_run_id VARCHAR(36), 
    PRIMARY KEY (id), 
    CONSTRAINT ck_analytics_state_singleton CHECK (id = 1), 
    FOREIGN KEY(active_run_id) REFERENCES analytics_signal_runs (id), 
    FOREIGN KEY(published_run_id) REFERENCES analytics_signal_runs (id)
)

""")
    op.execute("""
CREATE TABLE analytics_signals (
    id VARCHAR(36) NOT NULL, 
    identity_kind VARCHAR(32) NOT NULL, 
    analytical_client_key VARCHAR(512) NOT NULL, 
    normalization_version INTEGER NOT NULL, 
    display_name VARCHAR(512) NOT NULL, 
    known_phones JSON NOT NULL, 
    family VARCHAR(32) NOT NULL, 
    signal_type VARCHAR(32) NOT NULL, 
    episode_no INTEGER NOT NULL, 
    status VARCHAR(20) NOT NULL, 
    mode VARCHAR(16) NOT NULL, 
    severity INTEGER NOT NULL, 
    score NUMERIC(5, 2) NOT NULL, 
    confidence NUMERIC(5, 4) NOT NULL, 
    metrics JSON NOT NULL, 
    metrics_schema_version INTEGER NOT NULL, 
    generator_version VARCHAR(32) NOT NULL, 
    parameters_version INTEGER NOT NULL, 
    source_period_start DATE NOT NULL, 
    source_period_end DATE NOT NULL, 
    as_of DATE NOT NULL, 
    source_coverage_status VARCHAR(20) NOT NULL, 
    source_freshness_status VARCHAR(20) NOT NULL, 
    anchor_purchase_date DATE NOT NULL, 
    detected_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    last_evaluated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    resolved_at TIMESTAMP WITH TIME ZONE, 
    resolution_reason VARCHAR(64), 
    revision BIGINT NOT NULL, 
    run_id VARCHAR(36) NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT ck_analytics_identity_kind CHECK (identity_kind IN ('normalized_name','external_id')), 
    CONSTRAINT ck_analytics_signal_status CHECK (status IN ('ACTIVE','SUSPENDED','RESOLVED')), 
    CONSTRAINT ck_analytics_signal_scores CHECK (score BETWEEN 0 AND 100 AND confidence BETWEEN 0 AND 1), 
    CONSTRAINT ck_analytics_resolution CHECK ((status = 'RESOLVED') = (resolved_at IS NOT NULL)), 
    CONSTRAINT ck_analytics_shadow_only CHECK (mode = 'SHADOW'), 
    CONSTRAINT uq_analytics_episode UNIQUE (identity_kind, normalization_version, analytical_client_key, family, episode_no), 
    FOREIGN KEY(run_id) REFERENCES analytics_signal_runs (id)
)

""")
    op.execute("CREATE UNIQUE INDEX uq_analytics_open_episode ON analytics_signals (identity_kind, normalization_version, analytical_client_key, family) WHERE status IN ('ACTIVE','SUSPENDED')")
    op.execute("""
CREATE TABLE analytics_signal_changes (
    id BIGSERIAL NOT NULL, 
    signal_id VARCHAR(36) NOT NULL, 
    signal_revision BIGINT NOT NULL, 
    event_type VARCHAR(20) NOT NULL, 
    changed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    run_id VARCHAR(36) NOT NULL, 
    payload JSON NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_analytics_signal_revision UNIQUE (signal_id, signal_revision), 
    FOREIGN KEY(signal_id) REFERENCES analytics_signals (id), 
    FOREIGN KEY(run_id) REFERENCES analytics_signal_runs (id)
)

""")
    op.execute('CREATE INDEX ix_analytics_signal_changes_signal_id ON analytics_signal_changes (signal_id)')


def downgrade():
    op.drop_table('analytics_signal_changes')
    op.drop_table('analytics_signals')
    op.drop_table('analytics_signal_state')
    op.drop_table('analytics_purchase_days')
    op.drop_table('analytics_observed_phones')
    op.drop_table('analytics_client_evaluations')
    op.drop_table('analytics_signal_runs')
