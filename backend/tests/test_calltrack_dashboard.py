import asyncio
from unittest.mock import AsyncMock
from sqlalchemy.dialects import postgresql
from app.schemas.calltrack import CalltrackDashboardSalesRequest
from app.services import calltrack_dashboard as service


def test_query_aggregates_items_before_headers_and_limits_year():
    sql = str(service.monthly_sales_query(2026, ['client']).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert 'sum(dashboard_scope.total_amount)' in sql
    assert 'count(dashboard_scope.id)' in sql
    assert 'sum(sale_items.quantity)' in sql
    assert 'GROUP BY sale_items.sale_id' not in sql
    assert sql.count('GROUP BY EXTRACT(year FROM dashboard_scope.sale_date), EXTRACT(month FROM dashboard_scope.sale_date)') == 2
    assert '2026-01-01' in sql and '2027-01-01' in sql
    assert "= ANY (CAST(ARRAY['client']" in sql


def test_selected_manager_scope_and_zero_months(monkeypatch):
    from datetime import date
    monkeypatch.setattr(service,"business_today",lambda:date(2026,10,19))
    monkeypatch.setattr(service, 'get_client_managers', AsyncMock(return_value={'one':'Alice', 'two':'Bob'}))
    class Result:
        def mappings(self):
            return [{'year':2026, 'month':2, 'revenue':100, 'checks':1, 'units':3}]
    db = AsyncMock()
    db.execute.return_value = Result()
    result = asyncio.run(service.dashboard_sales(db, CalltrackDashboardSalesRequest(year=2026,managers=['Alice'])))
    assert len(result['items']) == 12
    assert result['items'][0]['checks'] == 0 and result['items'][1]['checks'] == 1
    db.execute.assert_awaited_once()
    assert len(result["previous_items"])==12 and result["previous_year"]==2025
    sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect(),compile_kwargs={"literal_binds":True}))
    assert "'one'" in sql and "'two'" not in sql


def test_no_selection_does_not_query_or_load_directory(monkeypatch):
    loader=AsyncMock()
    monkeypatch.setattr(service,'get_client_managers',loader)
    db=AsyncMock()
    result=asyncio.run(service.dashboard_sales(db,CalltrackDashboardSalesRequest(year=2026,managers=[])))
    assert len(result['items'])==12
    loader.assert_not_awaited()
    db.execute.assert_not_awaited()


def test_real_sql_does_not_multiply_checks_or_revenue(monkeypatch):
    from sqlalchemy import func
    # SQLite-only membership adapter; aggregation SQL itself is executed intact.
    monkeypatch.setattr(service, "dashboard_client_condition", lambda names: func.lower(func.trim(func.regexp_replace(service.Sale.client, r"\s+", " ", "g"))).in_(names))
    import re
    from decimal import Decimal
    from sqlalchemy import create_engine, event, text
    engine=create_engine("sqlite://")
    @event.listens_for(engine,"connect")
    def functions(conn,_):
        conn.create_function("regexp_replace",4,lambda v,p,r,f:re.sub(p,r,v or ""))
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE sales (id INTEGER, sale_date DATE, client TEXT, total_amount NUMERIC)"))
        conn.execute(text("CREATE TABLE sale_items (sale_id INTEGER, quantity NUMERIC)"))
        conn.execute(text("INSERT INTO sales VALUES (1,'2026-01-01','One',100),(2,'2026-01-02','One',50),(3,'2025-01-01','One',900),(4,'2026-01-01','Other',800),(5,'2026-02-01','One',-10)"))
        conn.execute(text("INSERT INTO sale_items VALUES (1,2),(1,3),(2,1),(3,99),(4,99),(5,-1)"))
        rows=conn.execute(service.monthly_sales_query(2026,['one'])).mappings().all()
        assert len(rows)==2
        assert rows[0]['revenue']==Decimal('150') and rows[0]['checks']==2 and rows[0]['units']==6
        assert rows[1]['revenue']==Decimal('-10') and rows[1]['units']==-1


def test_comparison_query_has_two_year_bounds_and_selected_names():
    from datetime import date
    sql = str(service.monthly_sales_query(2026, ["one"], compare=True, as_of=date(2026,10,19))
              .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds":True}))
    assert "2025-01-01" in sql and "2026-10-20" in sql
    assert "GROUP BY EXTRACT(year FROM dashboard_scope.sale_date), EXTRACT(month FROM dashboard_scope.sale_date)" in sql
    assert sql.count("= ANY (CAST(ARRAY['one']") == 1


def test_business_date_uses_volgograd_at_utc_day_boundary(monkeypatch):
    from datetime import datetime, timezone, date
    class Clock:
        @staticmethod
        def now(zone):
            assert str(zone)=="Europe/Volgograd"
            return datetime(2026,10,18,22,30,tzinfo=timezone.utc).astimezone(zone)
    monkeypatch.setattr(service,"datetime",Clock)
    assert service.business_today()==date(2026,10,19)
