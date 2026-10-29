"""Brand options must come from the configured catalogue, not matched sales."""
import asyncio
from app.routers import meta, product_analytics
from app.config import settings


def test_brands_alias_uses_remote_catalogue_when_local_catalogue_disabled(monkeypatch):
    calls = []

    async def remote(field, search):
        calls.append((field, search))
        return ["Alpha", "Beta"]

    monkeypatch.setattr(settings, "product_analytics_local_catalog_enabled", False)
    monkeypatch.setattr(product_analytics, "_catalog_options", remote)
    # No local table/lookup is needed in this mode.
    assert asyncio.run(meta.report_brands(search="Be", db=object())) == ["Alpha", "Beta"]
    assert calls == [("brand", "Be")]


def test_brands_alias_uses_all_local_catalogue_brands_not_only_matched_sales(monkeypatch):
    async def local(db, field, search):
        assert field == "brand"
        assert search == ""
        return ["Brand without a matched sale"]

    monkeypatch.setattr(settings, "product_analytics_local_catalog_enabled", True)
    monkeypatch.setattr(product_analytics, "local_options", local)
    assert asyncio.run(meta.report_brands(db=object())) == ["Brand without a matched sale"]
