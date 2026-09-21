from datetime import date
from app.services.product_report_utils import percent_change,previous_period,product_key

def test_product_identity_prefers_code_then_article():
 assert product_key("ART-1"," CODE-1 ","Одинаковое имя")=="code:code-1"
 assert product_key(" ART-1 ",None,"Одинаковое имя")=="article:art-1"
 assert product_key(None,None," Товар ")=="name:товар"

def test_previous_period_has_equal_duration():
 assert previous_period(date(2026,9,1),date(2026,9,20))==(date(2026,8,12),date(2026,8,31))

def test_percent_change_handles_zero():
 assert percent_change(10,0) is None
 assert percent_change(125,100)==25
