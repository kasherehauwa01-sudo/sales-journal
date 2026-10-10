"""Execute only the new revision against a disposable in-memory database."""

import importlib.util
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from app.models import CatalogLookup, CatalogProduct, CatalogSyncState


def test_revision_upgrade_matches_models_and_downgrade_is_local_only():
    path = (
        Path(__file__).parents[1] / "alembic/versions/20261010_15_catalog_attributes.py"
    )
    spec = importlib.util.spec_from_file_location("catalog_revision", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    assert revision.down_revision == "20261006_14"
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            revision.upgrade()
            inspector = inspect(connection)
            assert set(inspector.get_table_names()) == {
                "catalog_products",
                "catalog_lookups",
                "catalog_sync_state",
            }
            for model in (CatalogProduct, CatalogLookup, CatalogSyncState):
                columns = inspector.get_columns(model.__tablename__)
                assert {col["name"]: col["nullable"] for col in columns} == {
                    col.name: col.nullable for col in model.__table__.columns
                }
            assert (
                connection.scalar(
                    text("SELECT revision FROM catalog_sync_state WHERE id=1")
                )
                == 0
            )
            assert {
                index["name"] for index in inspector.get_indexes("catalog_lookups")
            } == {
                "ix_catalog_lookups_product_id",
                "ix_catalog_lookups_status",
                "ix_catalog_lookups_synced_at",
            }
            revision.downgrade()
            assert inspect(connection).get_table_names() == []
    engine.dispose()
