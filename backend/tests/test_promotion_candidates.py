from datetime import date

from app.services.promotion_candidates import TABLE_ROW_LIMIT, classify_candidate, percent_change, stock_months, table_rows

SETTINGS={"decline_percent":-30,"min_previous_units":5,"stale_days":30,"low_sales_units":2,"excess_stock_months":6,"min_stock":1}

def row(**values):
 base={"units":60,"units_30":4,"units_previous_30":10,"revenue_30":400,"revenue_previous_30":1000,"last_sale":date(2026,8,1),"analysis_days":180,"stock":100,"category_change_percent":10}
 return {**base,**values}

def test_percent_change_and_zero_base():
 assert percent_change(7,10)==-30
 assert percent_change(0,10)==-100
 assert percent_change(10,0) is None

def test_stock_months_and_division_by_zero():
 assert stock_months(60,10)==6
 assert stock_months(60,0) is None
 assert stock_months(None,10) is None

def test_decline_stale_excess_and_category_reasons_are_explainable():
 result=classify_candidate(row(),SETTINGS,date(2026,9,30))
 assert {"Продажи падают","Давно не продавался","Избыточный запас","Падает сильнее категории"}.issubset(result["reasons"])

def test_new_and_growing_products_receive_stop_factors():
 new=classify_candidate(row(analysis_days=20),SETTINGS,date(2026,9,30))
 growing=classify_candidate(row(units_30=20,units_previous_30=10),SETTINGS,date(2026,9,30))
 assert "Недостаточно истории" in new["stop_factors"]
 assert "Продажи растут без скидки" in growing["stop_factors"]

def test_missing_catalog_and_no_stock_do_not_remove_sales():
 missing=classify_candidate(row(stock=None,category="Не найдено в CatalogVR"),SETTINGS,date(2026,9,30))
 no_stock=classify_candidate(row(stock=0),SETTINGS,date(2026,9,30))
 assert missing["category"]=="Не найдено в CatalogVR"
 assert "Нет в наличии" in no_stock["stop_factors"]

def test_table_contains_no_more_than_300_rows():
 rows=[{"key":index} for index in range(450)]
 assert len(table_rows(rows))==TABLE_ROW_LIMIT==300
 assert table_rows(rows)[-1]["key"]==299
