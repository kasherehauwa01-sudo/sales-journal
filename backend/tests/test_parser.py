from datetime import date
from decimal import Decimal
from app.importer.parser import LEGACY_COLUMNS,decimal,find_header,identify_column,include_document,include_for_filename,normalize_sale,parse_items,read_sales
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


def test_negative_percent_is_not_scaled_by_one_hundred():
 assert decimal("-17%",percent=True)==Decimal("-17")
 assert decimal("-15",percent=True)==Decimal("-15")
 row=normalize_sale({**BASE,"discount_percent":"-17%","discount_card_percent":"-15"})
 assert row["discount_percent"]==Decimal("-17")
 assert row["discount_card_percent"]==Decimal("-15")


def test_excel_fraction_is_converted_to_percent():
 assert decimal(-0.17,percent=True)==Decimal("-17.00")
 assert decimal(0.17,percent=True)==Decimal("17.00")
 assert decimal("0,5%",percent=True)==Decimal("0.5")
 assert decimal("-0,6",percent=True)==Decimal("-0.6")

def test_aviators_file_only_includes_aviators_department():
 assert include_for_filename("Продажи Авиаторов.xlsx",{"department":" АВИАТОРОВ "})
 assert not include_for_filename("Продажи Авиаторов.xlsx",{"department":"Центр"})
 assert include_for_filename("Общие продажи.xlsx",{"department":"Центр"})

def test_return_and_internal_document_prefixes_are_skipped():
 for number in ("ВЗВ-0001"," рнв-42 ","ВРМ-7"):
  assert not include_document({"document_number":number})
 assert include_document({"document_number":"РН-123"})

def test_html_export_is_read_like_excel(tmp_path):
 path=tmp_path/"РеестрРН_Авиаторов.html"
 path.write_text("""<!doctype html><html><head><meta charset="utf-8"></head><body><table>
 <tr><th>№ п/п</th><th>Дата</th><th>№ Док.</th><th>Клиент</th><th>Подразделение</th><th>Сумма</th><th>Товары</th></tr>
 <tr><td>1</td><td>22.09.2026</td><td>РН-42</td><td>ООО Тест</td><td>Авиаторов</td><td>1 245,50</td><td>Артикул: 1<br>Код: A-1</td></tr>
 </table></body></html>""",encoding="utf-8")
 sheet,rows=read_sales(path)
 assert sheet=="Таблица 1"
 assert rows[0][0]==2
 assert rows[0][1]["document_number"]=="РН-42"
 assert rows[0][1]["products"]=="Артикул: 1\nКод: A-1"

def test_windows_1251_html_export_is_supported(tmp_path):
 path=tmp_path/"sales.htm"
 html='<meta http-equiv="Content-Type" content="text/html; charset=windows-1251"><table><tr><td>Дата</td><td>№ Док.</td><td>Подразделение</td><td>Сумма</td></tr><tr><td>22.09.2026</td><td>РН-1</td><td>Европа</td><td>100</td></tr></table>'
 path.write_bytes(html.encode("windows-1251"))
 _,rows=read_sales(path)
 assert rows[0][1]["department"]=="Европа"

def test_utf16_html_export_is_supported(tmp_path):
 path=tmp_path/"sales.html"
 html='<html><table><tr><td>Дата</td><td>№ Док.</td><td>Подразделение</td><td>Сумма</td></tr><tr><td>22.09.2026</td><td>РН-2</td><td>Авиаторов</td><td>200</td></tr></table></html>'
 path.write_bytes(html.encode("utf-16"))
 _,rows=read_sales(path)
 assert rows[0][1]["document_number"]=="РН-2"

def test_utf16_big_endian_html_without_bom_is_supported(tmp_path):
 path=tmp_path/"sales.html"
 html='<table><tr><td>Дата</td><td>№ Док.</td><td>Подразделение</td><td>Сумма</td></tr><tr><td>23.09.2026</td><td>РН-4</td><td>Авиаторов</td><td>400</td></tr></table>'
 path.write_bytes(html.encode("utf-16-be"))
 _,rows=read_sales(path)
 assert rows[0][1]["document_number"]=="РН-4"

def test_mhtml_export_with_quoted_printable_html_is_supported(tmp_path):
 path=tmp_path/"EROOR_РеестрРН_Авиаторов_23.09.2026.html"
 path.write_bytes('''MIME-Version: 1.0\r
Content-Type: multipart/related; boundary="report-boundary"\r
\r
--report-boundary\r
Content-Type: text/html; charset=windows-1251\r
Content-Transfer-Encoding: quoted-printable\r
\r
<html><table><tr><td>=C4=E0=F2=E0</td><td>=B9 =C4=EE=EA.</td><td>=CF=EE=E4=F0=E0=E7=E4=E5=EB=E5=ED=E8=E5</td><td>=D1=F3=EC=EC=E0</td></tr><tr><td>23.09.2026</td><td>=D0=CD-5</td><td>=C0=E2=E8=E0=F2=EE=F0=EE=E2</td><td>500</td></tr></table></html>\r
--report-boundary--\r
'''.encode("ascii"))
 _,rows=read_sales(path)
 assert rows[0][1]["document_number"]=="РН-5"
 assert rows[0][1]["department"]=="Авиаторов"

def test_mhtml_imports_html_marked_as_binary_attachment(tmp_path):
 path=tmp_path/"binary-part.html"
 html='<table><tr><td>Дата</td><td>№ Док.</td><td>Подразделение</td><td>Сумма</td></tr><tr><td>23.09.2026</td><td>РН-7</td><td>Авиаторов</td><td>700</td></tr></table>'
 import base64
 payload=base64.b64encode(html.encode("windows-1251")).decode("ascii")
 path.write_text(f'''MIME-Version: 1.0\nContent-Type: multipart/related; boundary="report"\n\n--report\nContent-Type: application/octet-stream; charset=windows-1251\nContent-Transfer-Encoding: base64\n\n{payload}\n--report--\n''',encoding="ascii")
 _,rows=read_sales(path)
 assert rows[0][1]["document_number"]=="РН-7"

def test_escaped_html_document_is_supported(tmp_path):
 path=tmp_path/"escaped.html"
 path.write_text('&lt;table&gt;&lt;tr&gt;&lt;td&gt;Дата&lt;/td&gt;&lt;td&gt;№ Док.&lt;/td&gt;&lt;td&gt;Подразделение&lt;/td&gt;&lt;td&gt;Сумма&lt;/td&gt;&lt;/tr&gt;&lt;tr&gt;&lt;td&gt;23.09.2026&lt;/td&gt;&lt;td&gt;РН-8&lt;/td&gt;&lt;td&gt;Авиаторов&lt;/td&gt;&lt;td&gt;800&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;',encoding="utf-8")
 _,rows=read_sales(path)
 assert rows[0][1]["document_number"]=="РН-8"

def test_html_rows_are_recovered_without_table_container(tmp_path):
 path=tmp_path/"broken-report.html"
 path.write_text('<TR><TD>Дата</TD><TD>№ Док.</TD><TD>Подразделение</TD><TD>Сумма</TD></TR><TR><TD>23.09.2026</TD><TD>РН-9</TD><TD>Авиаторов</TD><TD>900</TD></TR>',encoding="utf-8")
 _,rows=read_sales(path)
 assert rows[0][1]["document_number"]=="РН-9"

def test_tab_separated_report_disguised_as_html_is_supported(tmp_path):
 path=tmp_path/"text-report.html"
 path.write_text('Дата\t№ Док.\tПодразделение\tСумма\n23.09.2026\tРН-10\tАвиаторов\t1000\n',encoding="utf-8")
 _,rows=read_sales(path)
 assert rows[0][1]["document_number"]=="РН-10"

def test_truncated_html_table_is_still_imported(tmp_path):
 path=tmp_path/"sales.html"
 path.write_text('<table><tr><td>Дата</td><td>№ Док.</td><td>Подразделение</td><td>Сумма</td></tr><tr><td>23.09.2026</td><td>РН-6</td><td>Авиаторов</td><td>600</td>',encoding="utf-8")
 _,rows=read_sales(path)
 assert rows[0][1]["document_number"]=="РН-6"

def test_spreadsheet_xml_tags_inside_html_are_supported(tmp_path):
 path=tmp_path/"sales.html"
 path.write_text('''<?xml version="1.0"?><Workbook xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table>
 <Row><Cell><Data>Дата</Data></Cell><Cell><Data>№ Док.</Data></Cell><Cell><Data>Подразделение</Data></Cell><Cell><Data>Сумма</Data></Cell></Row>
 <Row><Cell><Data>22.09.2026</Data></Cell><Cell><Data>РН-3</Data></Cell><Cell><Data>Авиаторов</Data></Cell><Cell><Data>300</Data></Cell></Row>
 </Table></Worksheet></Workbook>''',encoding="utf-8")
 _,rows=read_sales(path)
 assert rows[0][1]["document_number"]=="РН-3"

def test_certificate_is_subtracted_from_total_and_keeps_legacy_fingerprint():
 row=normalize_sale({**BASE,"certificate_amount":"245,50"})
 assert row["total_amount"]==Decimal("1000.00")
 assert row["certificate_amount"]==Decimal("245.50")
 assert row["legacy_fingerprint"]==normalize_sale(BASE)["fingerprint"]
