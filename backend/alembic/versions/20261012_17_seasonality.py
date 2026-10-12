"""Add only compact seasonal aggregates, profiles and disabled durable control."""

from alembic import op
import sqlalchemy as sa

revision = "20261012_17"
down_revision = "20261010_16"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "seasonality_months",
        sa.Column("generation", sa.Integer(), primary_key=True),
        sa.Column("identity", sa.String(64), primary_key=True),
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Integer(), primary_key=True),
        sa.Column("incomplete_source", sa.Boolean(), nullable=False),
        sa.Column("units", sa.Numeric(30, 3), nullable=False),
        sa.Column("positive_lines", sa.BigInteger(), nullable=False),
        sa.Column("first_sale", sa.Date(), nullable=False),
        sa.Column("last_sale", sa.Date(), nullable=False),
    )
    op.create_table(
        "seasonality_profiles",
        sa.Column("generation", sa.Integer(), primary_key=True),
        sa.Column("identity", sa.String(64), primary_key=True),
        sa.Column("product_key", sa.Text(), nullable=False),
        sa.Column("name_min", sa.String(64)),
        sa.Column("name_max", sa.String(64)),
        sa.Column("coverage", sa.BigInteger(), nullable=False),
        sa.Column("dirty", sa.Boolean(), nullable=False),
        sa.Column("data", sa.JSON()),
        sa.Column("calculated_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_seasonality_pending",
        "seasonality_profiles",
        ["generation", "dirty", "identity"],
    )
    op.create_table(
        "seasonality_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("stop_requested", sa.Boolean(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *[
            sa.Column(name, sa.Integer(), nullable=False)
            for name in ("active_generation", "generation")
        ],
        sa.Column("algorithm_version", sa.Integer(), nullable=False),
        sa.Column("first_year", sa.Integer()),
        sa.Column("phase", sa.String(16), nullable=False),
        *[
            sa.Column(name, sa.BigInteger(), nullable=False)
            for name in (
                "cursor",
                "source_cursor",
                "upper_id",
                "coverage",
                "source_revision",
                "job_revision",
                "calculated",
            )
        ],
        sa.Column("rebuild_required", sa.Boolean(), nullable=False),
        sa.Column("last_checked", sa.Date()),
        sa.Column("last_success", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(32))
    )
    op.execute(
        "INSERT INTO seasonality_state (id,stop_requested,failure_count,enabled,active_generation,generation,algorithm_version,phase,cursor,source_cursor,upper_id,coverage,source_revision,job_revision,rebuild_required,calculated) VALUES (1,false,0,false,0,0,0,'idle',0,0,0,0,0,0,true,0)"
    )


def downgrade():
    op.drop_table("seasonality_profiles")
    op.drop_table("seasonality_months")
    op.drop_table("seasonality_state")
