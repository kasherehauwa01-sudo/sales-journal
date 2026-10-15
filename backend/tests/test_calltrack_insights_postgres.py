"""Real PostgreSQL regression; opt in with CALLTRACK_TEST_POSTGRES.

Creates/drops only a random isolated schema, never existing tables.
"""
import asyncio
import os
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings
from app.database import get_db
from app.models import ImportBatch, Sale, SaleItem, CatalogProduct, CatalogLookup
from app.routers.calltrack_integration import router
from app.services import calltrack_insights
from app.services.catalog_attributes import identity


@pytest.mark.skipif(not os.environ.get('CALLTRACK_TEST_POSTGRES'), reason='requires isolated PostgreSQL test server')
def test_real_database_and_authenticated_endpoint(monkeypatch):
    monkeypatch.setattr(settings, 'calltrack_integration_token', 'fixture-token')
    monkeypatch.setattr(calltrack_insights, '_rfm_snapshot', None)
    async def run():
        schema = 'test_insights_' + uuid4().hex
        engine = create_async_engine(os.environ['CALLTRACK_TEST_POSTGRES'], connect_args={'server_settings': {'search_path': schema}})
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA {schema}'))
                for model in (ImportBatch, Sale, SaleItem, CatalogProduct, CatalogLookup):
                    await connection.run_sync(lambda sync, table=model.__table__: table.create(sync))
                await connection.execute(ImportBatch.__table__.insert().values(id=1, filename='fixture', stored_path='fixture', file_size=0, status='done'))
                name = 'Бережнова Елена Сергеевна ИП'
                for sale_id, client, day, amount in [(1, name, date(2026, 1, 1), 10), (2, name, date(2026, 10, 14), 30), (3, 'Другой клиент', date(2025, 1, 1), 5)]:
                    await connection.execute(Sale.__table__.insert().values(id=sale_id, import_id=1, sale_date=day, document_number=str(sale_id), client=client, department='Тест', total_amount=amount, fingerprint=str(sale_id), social=False))
                for sale_id, code, quantity, price in [(1, 'A', 2, 5), (2, 'Alias', 3, 10), (3, 'Other', 1, 5)]:
                    await connection.execute(SaleItem.__table__.insert().values(id=sale_id, sale_id=sale_id, code=code, name='Товар', quantity=quantity, actual_price=price))
                await connection.execute(CatalogProduct.__table__.insert().values(product_id=7, code='A', category='Посуда', horeca=False, source_updated_at=datetime.now(timezone.utc), synced_at=datetime.now(timezone.utc)))
                for code in ['A', 'Alias']:
                    await connection.execute(CatalogLookup.__table__.insert().values(identity=identity(code, None), code=code, product_id=7, status='matched', matched_by='code'))
            session = async_sessionmaker(engine)
            async def database():
                async with session() as db:
                    yield db
            app = FastAPI()
            app.include_router(router, prefix='/api')
            app.dependency_overrides[get_db] = database
            async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
                payload = {'name': name, 'as_of': '2026-10-15'}
                assert (await client.post('/api/integrations/calltrack/client-insights', json=payload)).status_code == 401
                response = await client.post('/api/integrations/calltrack/client-insights', json=payload, headers={'Authorization': 'Bearer fixture-token'})
                assert response.status_code == 200, response.text
                data = response.json()
                assert data['unique_products'] == 1
                assert data['top_categories'] == [{'category': 'Посуда', 'revenue': '40.00', 'quantity': '5.000'}]
                assert sorted((row['sale_id'], row['quantity']) for row in data['sale_quantities']) == [(1, '2.000'), (2, '3.000')]
                assert data['rfm']['segment'] == 'Постоянные активные'
                assert len(data['rfm']['code']) == 3
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
            await engine.dispose()
    asyncio.run(run())
