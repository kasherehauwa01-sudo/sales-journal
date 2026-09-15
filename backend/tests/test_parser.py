from datetime import date
from decimal import Decimal
from app.importer.parser import LEGACY_COLUMNS,find_header,identify_column,normalize_sale,parse_items
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


def test_screenshot_table_layout_has_all_columns():
 header=["№\nп/п","Дата","№ Док.","Клиент","Подразделение","Сумма","Сумма\nбазовых цен","Скидка\n%","Основание","Автор","Тип цены","% ДК","№ ДК","Социальная","Сумма сертификатов","Акция","Телефон","Товары"]
 data=[1,"14.09.26","Р-00623878","АСПЕКТ ООО","Авиаторов","4 016,64","4 184,00","-4%","Ленина","Селянина Т.А.","Оптовые",None,None,None,None,None,"8-903-373-06-94","Артикул: 294058 | Код: БА-016440 | Наименование: Сковорода | Количество: 2 | Цена базовая: 2092,00 | Цена: 2008,32"]
 result=find_header([header,data])
 assert result is not None
 header_index,mapping,_=result
 assert header_index==0
 assert tuple(mapping)==LEGACY_COLUMNS


def test_legacy_layout_fallback_for_broken_header_encoding():
 broken_header=[f"column-{index}" for index in range(18)]
 data=[1,"14.09.26","Р-00623878","АСПЕКТ ООО","Авиаторов","4 016,64","4 184,00","-4%",None,"Автор","Оптовые",None,None,None,None,None,"8-903-373-06-94","Товар"]
 result=find_header([broken_header,data])
 assert result is not None
 assert result[0]==0
 assert result[1]["products"]==17


def test_products_from_real_export_are_split_and_labels_removed():
 raw="Артикул: 294058 | Код: БА-016440 | Наименование: Сковорода | Количество: 2 | Цена базовая: 2092,00 | Цена: 2008,32 | Артикул: 2092,00 | Код: ТРС 3,5мм | Наименование: Трос | Количество: 1 | Цена базовая: 2092,00 | Цена: 2008,32"
 items=parse_items(raw)
 assert len(items)==2
 assert items[0]["article"]=="294058"
 assert items[0]["code"]=="БА-016440"
 assert items[0]["name"]=="Сковорода"
 assert items[0]["quantity"]==Decimal("2")
 assert items[0]["actual_price"]==Decimal("2008.32")
