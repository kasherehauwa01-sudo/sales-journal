"""Opt-in actual temp-table/index/date_trunc regression, owned synthetic PG schema."""

import asyncio
from datetime import date
from app.models import ImportBatch, Sale, SaleItem
from app.routers import category_analytics_report as report
from test_import_control import pg


def test_native_details_preserve_global_share_product_share_and_comparisons(
    pg, monkeypatch
):
    async def clients(*args):
        return None

    async def catalog(products):
        return [("001", "dup", "Чашки"), (None, "dup", "Тарелки")], {}

    monkeypatch.setattr(report, "_clients", clients)
    monkeypatch.setattr(report, "_catalog_map", catalog)

    async def run():
        async with pg[2]() as db:
            db.add(
                ImportBatch(id=1, filename="synthetic", file_size=1, status="completed")
            )
            await db.flush()
            db.add(
                Sale(
                    id=1,
                    sale_date=date(2026, 9, 1),
                    document_number="1",
                    department="Store",
                    total_amount=24.5,
                    fingerprint="synthetic",
                    import_id=1,
                    social=False,
                )
            )
            await db.flush()
            db.add_all(
                [
                    SaleItem(
                        id=1,
                        sale_id=1,
                        code="001",
                        article="dup",
                        name="Cup",
                        quantity=1,
                        actual_price=12.25,
                    ),
                    SaleItem(
                        id=2,
                        sale_id=1,
                        code=None,
                        article="dup",
                        name="Plate",
                        quantity=1,
                        actual_price=12.25,
                    ),
                ]
            )
            await db.commit()
            for mode in ("previous_period", "previous_year"):
                result = await report.details(
                    "Чашки",
                    date(2026, 9, 1),
                    date(2026, 9, 30),
                    comparison_mode=mode,
                    stores=[],
                    group_by="day",
                    db=db,
                )
                assert result["category"]["current_revenue"] == 12.25
                assert result["category"]["current_share"] == 50
                assert result["category"]["previous_revenue"] == 0
                assert (
                    len(result["products"]) == 1
                    and result["products"][0]["share"] == 100
                )
                assert result["products"][0]["code"] == "001"
                assert result["points"][0]["revenue"] == 12.25

    asyncio.run(run())
