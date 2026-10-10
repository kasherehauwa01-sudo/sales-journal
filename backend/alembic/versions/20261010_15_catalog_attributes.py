"""Local attributes and resumable manual reconciliation (no scheduled tasks)."""

from alembic import op
import sqlalchemy as sa

revision = "20261010_15"
down_revision = "20261006_14"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "catalog_products",
        sa.Column("product_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("article", sa.String(255)),
        *[
            sa.Column(name, sa.Text())
            for name in (
                "manufacturer",
                "brand",
                "category",
                "subcategory",
                "legacy_category",
                "material",
                "image_url",
            )
        ],
        sa.Column("category_id", sa.Integer()),
        sa.Column("horeca", sa.Boolean(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False)
    )
    op.create_table(
        "catalog_lookups",
        sa.Column("identity", sa.String(64), primary_key=True),
        sa.Column("code", sa.Text()),
        sa.Column("article", sa.Text()),
        sa.Column(
            "product_id", sa.Integer(), sa.ForeignKey("catalog_products.product_id")
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("matched_by", sa.String(16)),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("cycle", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','matched','not_found','ambiguous','invalid')",
            name="ck_catalog_lookup_status",
        ),
        sa.CheckConstraint(
            "status != 'matched' OR (product_id IS NOT NULL AND matched_by IS NOT NULL)",
            name="ck_catalog_lookup_matched",
        ),
    )
    for name in ("product_id", "status", "synced_at"):
        op.create_index("ix_catalog_lookups_" + name, "catalog_lookups", [name])
    op.create_table(
        "catalog_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        *[
            sa.Column(name, sa.BigInteger(), nullable=False)
            for name in (
                "revision",
                "cycle",
                "cursor",
                "upper_id",
                "processed",
                "matched",
                "not_found",
                "ambiguous",
                "errors",
                "invalid",
            )
        ],
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *[
            sa.Column(name, sa.DateTime(timezone=True))
            for name in (
                "started_at",
                "finished_at",
                "last_success_at",
                "last_full_success_at",
            )
        ],
        sa.Column("error_code", sa.String(32))
    )
    op.execute(
        "INSERT INTO catalog_sync_state (id,revision,cycle,cursor,upper_id,processed,matched,not_found,ambiguous,errors,invalid,completed,status) VALUES (1,0,0,0,0,0,0,0,0,0,0,true,'idle')"
    )


def downgrade():
    op.drop_table("catalog_lookups")
    op.drop_table("catalog_products")
    op.drop_table("catalog_sync_state")
