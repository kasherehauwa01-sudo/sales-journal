from datetime import date

from app.services.product_analytics import classify, group_rows, merge_periods, percent_change, previous_period, product_key, summary


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
