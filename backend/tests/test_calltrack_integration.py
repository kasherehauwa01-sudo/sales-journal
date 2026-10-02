import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from app.config import settings
from app.routers.calltrack_integration import require_calltrack_token, sale_detail
from app.schemas import ItemOut, SaleOut
from app.schemas.calltrack import CALLTRACK_BATCH_LIMIT, CalltrackClientRef, CalltrackSalesRequest
from app.services import calltrack_integration
from app.services.calltrack_integration import AmbiguousClientReference


def request(clients, start=date(2026, 9, 1), end=date(2026, 9, 30)):
    return CalltrackSalesRequest(clients=clients, date_from=start, date_to=end)


def sale(sale_id, *, client="ООО Ромашка", phone="+79991234567", day=5):
    return SimpleNamespace(
        id=sale_id, sale_date=date(2026, 9, day), document_number=str(sale_id),
        client=client, phone=phone, department="Магазин", total_amount=Decimal("15000"),
    )


def run_batch(monkeypatch, payload, sales):
    calls=[]
    async def find(_db, **kwargs):
        calls.append(kwargs);return sales
    async def managers():return {"ооо ромашка":"Менеджер"}
    monkeypatch.setattr(calltrack_integration,"find_client_sales",find)
    monkeypatch.setattr(calltrack_integration,"get_client_managers",managers)
    result=asyncio.run(calltrack_integration.find_sales_for_calltrack(object(),payload))
    return result,calls


def test_one_client_and_one_sale(monkeypatch):
    payload=request([CalltrackClientRef(key="c1",phone="8 (999) 123-45-67")])
    result,calls=run_batch(monkeypatch,payload,[sale(100)])
    assert [(row.sale_id,row.client_key,row.matched_by) for row in result]==[(100,"c1","phone")]
    assert len(calls)==1
    assert calls[0]["date_from"]==date(2026,9,1)
    assert calls[0]["date_to"]==date(2026,9,30)


def test_multiple_clients_and_sales_use_one_database_request(monkeypatch):
    payload=request([
        CalltrackClientRef(key="c1",phone="79991234567",name="ООО Ромашка"),
        CalltrackClientRef(key="c2",name="Иванов И.И."),
    ])
    result,calls=run_batch(monkeypatch,payload,[sale(100),sale(200),sale(300,client="Иванов И.И.",phone=None)])
    assert [row.sale_id for row in result]==[100,200,300]
    assert [row.client_key for row in result]==["c1","c1","c2"]
    assert len(calls)==1


def test_period_is_inclusive_and_excludes_outside_sales(monkeypatch):
    payload=request([CalltrackClientRef(key="c1",phone="79991234567")])
    candidates=[
        sale(1,day=1),sale(2,day=30),
        SimpleNamespace(**{**sale(3).__dict__,"sale_date":date(2026,8,31)}),
        SimpleNamespace(**{**sale(4).__dict__,"sale_date":date(2026,10,1)}),
    ]
    calls=[]
    async def find(_db,**kwargs):
        calls.append(kwargs)
        return [row for row in candidates if kwargs["date_from"]<=row.sale_date<=kwargs["date_to"]]
    async def managers():return {}
    monkeypatch.setattr(calltrack_integration,"find_client_sales",find)
    monkeypatch.setattr(calltrack_integration,"get_client_managers",managers)
    result=asyncio.run(calltrack_integration.find_sales_for_calltrack(object(),payload))
    assert [row.sale_id for row in result]==[1,2]
    assert len(calls)==1


def test_sale_is_deduplicated_when_phone_and_name_match(monkeypatch):
    payload=request([CalltrackClientRef(key="c1",phone="79991234567",name="ООО Ромашка")])
    same=sale(100)
    result,_=run_batch(monkeypatch,payload,[same,same])
    assert [row.sale_id for row in result]==[100]
    assert result[0].matched_by=="phone"


def test_similar_names_do_not_match(monkeypatch):
    payload=request([CalltrackClientRef(key="c1",name="ООО Ромашка")])
    result,_=run_batch(monkeypatch,payload,[sale(100,client="ООО Ромашка Плюс",phone=None)])
    assert result==[]


def test_no_sales_is_successful_empty_result(monkeypatch):
    payload=request([CalltrackClientRef(key="c1",name="Неизвестный клиент")])
    result,calls=run_batch(monkeypatch,payload,[])
    assert result==[] and len(calls)==1


def test_ambiguous_identifiers_are_rejected(monkeypatch):
    payload=request([
        CalltrackClientRef(key="c1",phone="79991234567"),
        CalltrackClientRef(key="c2",phone="8 999 123-45-67"),
    ])
    with pytest.raises(AmbiguousClientReference):
        run_batch(monkeypatch,payload,[])


def test_invalid_period_and_batch_limit_are_rejected():
    with pytest.raises(ValidationError,match="date_from"):
        request([CalltrackClientRef(key="c1",name="Клиент")],date(2026,10,1),date(2026,9,30))
    clients=[CalltrackClientRef(key=str(index),name=f"Клиент {index}") for index in range(CALLTRACK_BATCH_LIMIT+1)]
    with pytest.raises(ValidationError):request(clients)


def test_authentication_requires_correct_bearer_token(monkeypatch):
    monkeypatch.setattr(settings,"calltrack_integration_token","secret")
    for credentials in (None,HTTPAuthorizationCredentials(scheme="Bearer",credentials="wrong")):
        with pytest.raises(HTTPException) as caught:require_calltrack_token(credentials)
        assert caught.value.status_code==401
    assert require_calltrack_token(HTTPAuthorizationCredentials(scheme="Bearer",credentials="secret")) is None


def test_detail_returns_full_sale_and_items(monkeypatch):
    detail=SaleOut(
        id=1,row_number=None,sale_date=date(2026,9,5),document_number="100",client="Клиент",
        manager="Менеджер",department="Магазин",total_amount=Decimal("100"),base_amount=Decimal("120"),
        discount_percent=Decimal("10"),reason=None,author="Автор",price_type="Розница",
        discount_card_percent=None,discount_card_number=None,social=False,certificate_amount=None,
        promotion=None,phone="+79991234567",original_products_text="Товар",created_at=datetime.now(timezone.utc),
        items=[ItemOut(id=2,article="A",code="1",name="Товар",quantity=Decimal("1"),base_price=Decimal("120"),actual_price=Decimal("100"))],
    )
    async def loader(_db,_sale_id):return detail
    monkeypatch.setattr("app.routers.calltrack_integration.load_sale_detail",loader)
    response=asyncio.run(sale_detail(1,object()))
    assert response.id==1 and response.manager=="Менеджер" and response.items[0].name=="Товар"


def test_unknown_detail_returns_404(monkeypatch):
    async def loader(_db,_sale_id):return None
    monkeypatch.setattr("app.routers.calltrack_integration.load_sale_detail",loader)
    with pytest.raises(HTTPException) as caught:asyncio.run(sale_detail(999,object()))
    assert caught.value.status_code==404
