from datetime import date
from app.services.product_report_utils import localized_summary_rows,percent_change,previous_period,product_key

def test_product_identity_prefers_code_then_article():
 assert product_key("ART-1"," CODE-1 ","Одинаковое имя")=="code:code-1"
 assert product_key(" ART-1 ",None,"Одинаковое имя")=="article:art-1"
 assert product_key(None,None," Товар ")=="name:товар"

def test_previous_period_has_equal_duration():
 assert previous_period(date(2026,9,1),date(2026,9,20))==(date(2026,8,12),date(2026,8,31))

def test_percent_change_handles_zero():
 assert percent_change(10,0) is None
 assert percent_change(125,100)==25

def test_excel_summary_labels_are_localized():
 rows=dict(localized_summary_rows({"revenue":100,"units":2,"checks":1,"clients":1,"average_price":50,"items_per_check":2,"discount_amount":10,"average_discount":5}))
 assert set(rows)=={"Продажи, ₽","Продано, шт.","Количество чеков","Уникальных клиентов","Средняя цена продажи","Среднее количество единиц в чеке","Сумма скидки, ₽","Средний процент скидки"}
