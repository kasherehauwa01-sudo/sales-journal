from datetime import date
from decimal import Decimal
from app.importer.parser import find_header,identify_column,normalize_sale,parse_items
BASE={"sale_date":"14.09.2026","document_number":"42","department":"Европа","total_amount":"1 245,50","products":"A1 | C1 | Крем | 2 | 700 | 622,75"}
def test_normalization_and_items():
 row=normalize_sale(BASE);assert row["sale_date"]==date(2026,9,14);assert row["total_amount"]==Decimal("1245.50");assert row["items"][0]["quantity"]==Decimal("2")
def test_fingerprint_is_stable():
 assert normalize_sale(BASE)["fingerprint"]==normalize_sale({**BASE,"department":"  ЕВРОПА "})["fingerprint"]
def test_plain_product_is_not_lost():assert parse_items("Неизвестный формат")[0]["name"]=="Неизвестный формат"


def test_header_variants_are_recognized():
 assert identify_column(" №Док. \n") == "document_number"
 assert identify_column("Дата продажи") == "sale_date"
 assert identify_column("Сумма продажи, руб.") == "total_amount"


def test_multiline_header_is_found():
 rows=[
  ["Отчёт о продажах"],
  ["Дата документа", "№Док."],
  [None, None, "Торговая точка", "Сумма продажи, руб."],
  ["14.09.2026", "42", "Европа", 100],
 ]
 result=find_header(rows)
 assert result is not None
 header_index,mapping,_=result
 assert header_index==2
 assert set(mapping)>= {"sale_date","document_number","department","total_amount"}


def test_incomplete_header_returns_diagnostics():
 result=find_header([["Дата", "Клиент"]])
 assert result is not None
 header_index,_,recognized=result
 assert header_index==-1
 assert recognized=={"sale_date","client"}
