import asyncio
from datetime import date
from decimal import Decimal

from sqlalchemy.dialects import postgresql

from app.services import calltrack_insights as service
from app.services.rfm import SegmentSettings, enrich


def test_unique_products_are_lifetime_distinct_and_categories_are_ranked():
    rows = [dict(key='1', code='A', name='A', revenue=Decimal('0.10'), quantity=Decimal('1.250')),
            dict(key='2', code='Alias', name='A', revenue=Decimal('0.20'), quantity=Decimal('2.750')),
            dict(key='3', code='B', name='B', revenue=Decimal('2.00'), quantity=Decimal('1'))]
    catalog = {'1': {'product_id': 7, 'catalog_category': 'Посуда'}, '2': {'product_id': 7, 'catalog_category': 'Посуда'}}
    result = service.product_summary(rows, catalog)
    assert result['unique_products'] == 2
    assert result['uncategorized_products'] == 1
    assert result['top_categories'] == [
        {'category': 'Без категории', 'revenue': Decimal('2.00'), 'quantity': Decimal('1')},
        {'category': 'Посуда', 'revenue': Decimal('0.30'), 'quantity': Decimal('4.000')},
    ]


def test_top_twenty_and_code_article_name_fallback():
    rows = [dict(key=str(i), article=str(i), revenue=Decimal(i), quantity=Decimal(1)) for i in range(25)]
    result = service.product_summary(rows, {str(i): {'catalog_category': str(i)} for i in range(25)})
    assert result['unique_products'] == 25
    assert len(result['top_categories']) == 20
    assert result['top_categories'][0]['category'] == '24'
    assert service.product_key({'key': '1', 'code': ' ', 'article': ' X '}, {}) == ('article', 'x')
    assert service.product_key({'key': '1', 'name': 'Название'}, {}) == ('name', 'название')


def test_population_reuses_existing_rfm_rules_and_caches_across_clients(monkeypatch):
    calls = []
    rows = [dict(client=f'Клиент {i}', recency_days=i*20, frequency=10-i, monetary=float(1000-i*90),
                 first_purchase=date(2025, 1, 1), cycle_overdue=i/2) for i in range(8)]
    async def load(db, start, end, departments):
        calls.append((start, end, departments))
        return [dict(row) for row in rows]
    monkeypatch.setattr(service, 'rfm_rows', load)
    monkeypatch.setattr(service, '_rfm_snapshot', None)
    async def run():
        first = await service.population_rfm(None, date(2026, 10, 15))
        second = await service.population_rfm(None, date(2026, 10, 15))
        assert first == second
        expected, _ = enrich([dict(row) for row in rows], date(2026, 10, 15), SegmentSettings())
        assert [first[row['client'].lower()]['segment'] for row in expected] == [row['segment'] for row in expected]
        assert [first[row['client'].lower()]['code'] for row in expected] == [row['rfm_code'] for row in expected]
    asyncio.run(run())
    assert calls == [(date.min, date(2026, 10, 15), None)]


def test_insights_queries_are_grouped_by_sale_and_product_not_per_receipt(monkeypatch):
    statements = []
    class Result:
        def __init__(self, rows): self.rows = rows
        def mappings(self): return self
        def all(self): return self.rows
    class DB:
        async def execute(self, query):
            statements.append(str(query.compile(dialect=postgresql.dialect())))
            return Result([{'sale_id': 1, 'quantity': Decimal('3.500')}] if len(statements) == 1 else
                          [{'code': 'a', 'article': None, 'name': 'Товар', 'quantity': Decimal('3.500'), 'revenue': Decimal('12.25')}])
    async def catalog(rows, db): return {'0': {'product_id': 7, 'catalog_category': 'Посуда'}}
    async def rfm(db, as_of): return {'клиент': {'segment': 'Новые', 'code': '333'}}
    monkeypatch.setattr(service, 'local_catalog', catalog)
    monkeypatch.setattr(service, 'population_rfm', rfm)
    result = asyncio.run(service.client_insights(DB(), 'Клиент', date(2026, 10, 15)))
    assert len(statements) == 2
    assert 'GROUP BY sales.id' in statements[0]
    assert 'GROUP BY sale_items.code' in statements[1]
    assert all('sales.phone' not in sql for sql in statements)
    assert result['unique_products'] == 1
    assert result['sale_quantities'][0]['quantity'] == Decimal('3.500')
    assert result['rfm']['segment'] == 'Новые'


def test_endpoint_requires_existing_integration_token():
    from app.routers.calltrack_integration import router, require_calltrack_token
    route = next(route for route in router.routes if route.path.endswith('/client-insights'))
    assert require_calltrack_token in [dependency.call for dependency in route.dependant.dependencies]
