from datetime import date
from decimal import Decimal
from app.importer.parser import fingerprint,normalize_sale,parse_items
BASE={"sale_date":"14.09.2026","document_number":"42","department":"Европа","total_amount":"1 245,50","products":"A1 | C1 | Крем | 2 | 700 | 622,75"}
def test_normalization_and_items():
 row=normalize_sale(BASE);assert row["sale_date"]==date(2026,9,14);assert row["total_amount"]==Decimal("1245.50");assert row["items"][0]["quantity"]==Decimal("2")
def test_fingerprint_is_stable():
 assert normalize_sale(BASE)["fingerprint"]==normalize_sale({**BASE,"department":"  ЕВРОПА "})["fingerprint"]
def test_plain_product_is_not_lost():assert parse_items("Неизвестный формат")[0]["name"]=="Неизвестный формат"
