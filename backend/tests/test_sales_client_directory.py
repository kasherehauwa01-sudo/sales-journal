import asyncio
from datetime import date
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models import Base, Sale, ImportBatch
from app.routers.sales_dynamics_report import client_directory


def test_search_beyond_first_thousand_keyset_pages_and_literal_search():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as db:
                db.add(
                    ImportBatch(
                        id=1, filename="synthetic", file_size=0, status="completed"
                    )
                )
                await db.flush()
                db.add_all(
                    [
                        Sale(
                            id=i + 1,
                            sale_date=date(2026, 1, 1),
                            document_number=str(i),
                            client=f"Client {i:04}",
                            department="Магазин",
                            total_amount=1,
                            fingerprint=str(i),
                            import_id=1,
                        )
                        for i in range(1050)
                    ]
                )
                db.add(
                    Sale(
                        id=1051,
                        sale_date=date(2026, 1, 1),
                        document_number="dup",
                        client=" client 0001 ",
                        department="Магазин",
                        total_amount=1,
                        fingerprint="dup",
                        import_id=1,
                    )
                )
                await db.commit()
                target = await client_directory(search="1049", db=db)
                assert target == {"items": ["Client 1049"], "next_cursor": None}
                names = []
                cursor = None
                for _ in range(12):
                    page = await client_directory(after=cursor, db=db)
                    assert len(page["items"]) <= 100
                    names += page["items"]
                    if page["next_cursor"] is None:
                        break
                    assert page["next_cursor"] != cursor
                    cursor = page["next_cursor"]
                assert (
                    len(names) == 1050
                    and len(set(name.strip().lower() for name in names)) == 1050
                )
                assert (await client_directory(search="%", db=db))["items"] == []
        finally:
            await engine.dispose()

    asyncio.run(run())
