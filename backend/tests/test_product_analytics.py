import asyncio
from datetime import date

import pytest
from fastapi import HTTPException

from app.routers import product_analytics as product_analytics_router
from app.services.product_analytics import catalog_property, classify, group_rows, merge_periods, percent_change, previous_period, product_key, summary
from app.services.vrcatalog import VrCatalogError


def row(article, revenue, units, checks, **extra):
    return {"key": product_key(article, None, article), "article": article, "code": None, "name": article, "revenue": revenue, "units": units, "checks": checks, "first_sale": date(2026, 9, 1), "last_sale": date(2026, 9, 20), **extra}


def test_products_are_classified_as_growth_decline_stopped_and_new():
    current=[row("рост",200,2,1),row("падение",50,1,1),row("новый",80,1,1)]
    old=[row("рост",100,1,1),row("падение",100,2,1),row("остановлен",70,1,1)]
    groups=classify(merge_periods(current,old,{}))
    assert [x["article"] for x in groups["growth"]]==["рост"]
    assert [x["article"] for x in groups["decline"]]==["падение"]
    assert [x["article"] for x in groups["stopped"]]==["остановлен"]
    assert [x["article"] for x in groups["new"]]==["новый"]


def test_new_product_has_no_infinite_percent():
    item=merge_periods([row("новый",100,1,1)],[],{})[0]
    assert item["revenue_change"] is None
    assert percent_change(100,0) is None


def test_missing_catalog_product_remains_visible():
    item=merge_periods([row("нет-в-каталоге",100,1,1)],[],{})[0]
    assert item["brand"]=="Не заполнено"
    assert item["category"]=="Не заполнено"


def test_catalog_card_fields_are_forwarded():
    catalog={"article:карточка":{"image_url":"https://example.test/image.jpg","properties":[{"name":"Цвет","value":"Белый"}],"stocks":[{"warehouse":"Основной","quantity":7}],"prices":[{"name":"Розница","value":1000}]}}
    item=merge_periods([row("карточка",100,1,1)],[],catalog)[0]
    assert item["image_url"].endswith("image.jpg")
    assert item["properties"][0]["value"]=="Белый"
    assert item["stocks"][0]["quantity"]==7
    assert item["prices"][0]["value"]==1000


def test_products_are_grouped_by_brand_and_category():
    catalog={"article:а":{"brand":"Бренд","category":"Категория"},"article:б":{"brand":"Бренд","category":"Категория"}}
    items=merge_periods([row("а",100,1,1),row("б",200,2,1)],[],catalog)
    brand=group_rows(items,"brand")[0];category=group_rows(items,"category")[0]
    assert (brand["revenue"],brand["units"],brand["sku"])==(300,3,2)
    assert (category["revenue"],category["sku"])==(300,2)


def test_returns_are_preserved_in_totals():
    rows=merge_periods([row("продажа",100,2,1),row("возврат",-40,-1,1)],[],{});data=summary(rows)
    assert data["revenue"]["current"]==60
    assert data["units"]["current"]==1
    assert [x["article"] for x in classify(rows)["new"]]==["продажа"]


def test_comparison_period_may_be_set_separately():
    assert previous_period(date(2026,9,1),date(2026,9,10))==(date(2026,8,22),date(2026,8,31))


def test_stable_key_does_not_use_name_when_article_exists():
    assert product_key(" 123 ",None,"Старое имя")==product_key("123",None,"Новое имя")


def test_category_supports_catalog_name_and_nested_group():
    assert catalog_property({"category_name": "Посуда"}, "category") == "Посуда"
    assert catalog_property({"group": {"name": "Хранение"}}, "category") == "Хранение"


def test_catalog_tree_endpoint_proxies_recursive_tree(monkeypatch):
    tree=[{"id":"root","code":"root","name":"Корень","parent_id":None,"children":[{"id":"child","code":"child","name":"Раздел","parent_id":"root","children":[]}]}]
    async def loader():return tree
    monkeypatch.setattr(product_analytics_router,"get_catalog_tree",loader)
    assert asyncio.run(product_analytics_router.catalog_tree())==tree


def test_catalog_tree_endpoint_returns_bad_gateway(monkeypatch):
    async def loader():raise VrCatalogError("vrcatalog недоступен")
    monkeypatch.setattr(product_analytics_router,"get_catalog_tree",loader)
    with pytest.raises(HTTPException) as caught:asyncio.run(product_analytics_router.catalog_tree())
    assert caught.value.status_code==502


def test_brand_options_endpoint_proxies_search_and_pagination(monkeypatch):
    calls=[]
    async def loader(**kwargs):calls.append(kwargs);return {"items":[{"value":"Pasabahce","label":"Pasabahce"}],"page":2,"page_size":50,"total":1,"pages":1}
    monkeypatch.setattr(product_analytics_router,"get_brands",loader)
    response=asyncio.run(product_analytics_router.brand_options(search="pasa",page=2,page_size=50))
    assert response["items"][0]["value"]=="Pasabahce"
    assert calls==[{"search":"pasa","page":2,"page_size":50}]


def test_dataset_applies_multiple_brands_and_sections_together(monkeypatch):
    calls=[]
    data=product_analytics_router.AnalyticsRequest(
        date_from=date(2026,9,1),date_to=date(2026,9,30),
        brands=["Pasabahce","Regent"],subcategories=["Посуда","Семена"],
    )
    async def period_rows(_data,_db,_start,_end):
        return [row("keep",100,1,1),row("drop",50,1,1)]
    async def filter_keys(**kwargs):calls.append(kwargs);return {"article:keep"}
    async def catalog(_rows):return {}
    monkeypatch.setattr(product_analytics_router,"_period_rows",period_rows)
    monkeypatch.setattr(product_analytics_router,"get_catalog_filter_keys",filter_keys)
    monkeypatch.setattr(product_analytics_router,"_catalog",catalog)

    result,_,_=asyncio.run(product_analytics_router._dataset(data,object()))

    assert [item["key"] for item in result]==["article:keep"]
    assert calls==[{"brands":["Pasabahce","Regent"],"sections":["Посуда","Семена"]}]


def test_dataset_with_empty_brand_and_section_filters_keeps_all_rows(monkeypatch):
    data=product_analytics_router.AnalyticsRequest(date_from=date(2026,9,1),date_to=date(2026,9,30))
    async def period_rows(_data,_db,_start,_end):return [row("first",100,1,1),row("second",50,1,1)]
    async def filter_keys(**kwargs):
        assert kwargs=={"brands":[],"sections":[]};return None
    async def catalog(_rows):return {}
    monkeypatch.setattr(product_analytics_router,"_period_rows",period_rows)
    monkeypatch.setattr(product_analytics_router,"get_catalog_filter_keys",filter_keys)
    monkeypatch.setattr(product_analytics_router,"_catalog",catalog)
    result,_,_=asyncio.run(product_analytics_router._dataset(data,object()))
    assert {item["key"] for item in result}=={"article:first","article:second"}
