import base64, hashlib, quopri, re
from email import policy
from email.parser import BytesParser
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator
from app.services.client_identity import normalize_phone
ALIASES = {
 "row_number":["№ п/п","n п/п","номер п/п"], "sale_date":["дата","дата продажи","дата документа"],
 "document_number":["№ док.","№ док","№док.","№док","n док.","№ документа","номер документа","документ"],
 "client":["клиент","покупатель","контрагент"], "department":["подразделение","магазин","торговая точка"],
 "total_amount":["сумма","сумма продажи","итого"], "base_amount":["сумма базовых цен","сумма по базовым ценам","базовая сумма"],
 "discount_percent":["скидка %","скидка%"], "reason":["основание"], "author":["автор"], "price_type":["тип цены"],
 "discount_card_percent":["% дк","дк %"], "discount_card_number":["№ дк","номер дк"], "social":["социальная"],
 "certificate_amount":["сумма сертификатов"], "promotion":["акция"], "phone":["телефон","номер телефона"], "products":["товары","состав продажи","номенклатура"]}
REQUIRED={"sale_date","document_number","department","total_amount"}
LEGACY_COLUMNS=("row_number","sale_date","document_number","client","department","total_amount","base_amount","discount_percent","reason","author","price_type","discount_card_percent","discount_card_number","social","certificate_amount","promotion","phone","products")
def norm(v:Any)->str:
 return re.sub(r"\s+"," ",str(v or "").replace("\xa0"," ").strip().lower().replace("ё","е"))
def include_for_filename(filename:str,raw:dict)->bool:
 """Файлы «Авиаторов» содержат свою выборку: из них берём только одноимённое подразделение."""
 return "авиаторов" not in norm(filename) or norm(raw.get("department"))=="авиаторов"
EXCLUDED_DOCUMENT_PREFIXES=("взв-","рнв-","врм-")
def include_document(raw:dict)->bool:
 """Исключает возвратные и внутренние документы до создания продажи."""
 return not norm(raw.get("document_number")).startswith(EXCLUDED_DOCUMENT_PREFIXES)
def compact(v:Any)->str:
 return re.sub(r"[^a-zа-я0-9%]+","",norm(v).replace("№","n"))
LOOKUP={norm(alias):key for key,aliases in ALIASES.items() for alias in aliases}
COMPACT_LOOKUP={compact(alias):key for key,aliases in ALIASES.items() for alias in aliases}
def identify_column(value:Any)->str|None:
 """Сопоставляет заголовок, сохраняя устойчивость к точкам, №, переносам и пояснениям."""
 normalized=norm(value)
 if normalized in LOOKUP:return LOOKUP[normalized]
 packed=compact(value)
 if packed in COMPACT_LOOKUP:return COMPACT_LOOKUP[packed]
 candidates=[(len(alias),key) for alias,key in COMPACT_LOOKUP.items() if len(alias)>=4 and (packed.startswith(alias) or packed.endswith(alias))]
 return max(candidates,default=(0,None))[1]
def find_header(rows:list[list[Any]],scan_limit:int=1000)->tuple[int,dict[str,int],set[str]]|None:
 """Ищет шапку, в том числе разбитую на несколько соседних строк."""
 recent:list[tuple[int,dict[str,int]]]=[];best:set[str]=set()
 for idx,row in enumerate(rows[:scan_limit]):
  current={key:i for i,value in enumerate(row) if (key:=identify_column(value))}
  if len(current)>len(best):best=set(current)
  recent.append((idx,current));recent=recent[-3:]
  merged:dict[str,int]={}
  for _,mapping in recent:merged.update(mapping)
  if REQUIRED.issubset(merged):return idx,merged,best
 # Резервный путь только для известной 18-колоночной выгрузки. Он нужен для старых XLS,
 # в которых xlrd иногда возвращает подписи шапки с повреждённой кодировкой.
 for idx,row in enumerate(rows[:scan_limit]):
  if len(row)<len(LEGACY_COLUMNS) or idx+1>=len(rows):continue
  header_cells=sum(bool(norm(value)) for value in row[:len(LEGACY_COLUMNS)])
  if header_cells>=14 and looks_like_legacy_sale(rows[idx+1]):
   return idx,{key:position for position,key in enumerate(LEGACY_COLUMNS)},best
 return None if not best else (-1,{},best)
def looks_like_legacy_sale(row:list[Any])->bool:
 if len(row)<len(LEGACY_COLUMNS):return False
 try:
  parse_date(row[1]);amount=decimal(row[5])
 except (TypeError,ValueError):return False
 return bool(str(row[2] or "").strip() and str(row[4] or "").strip() and amount is not None)
def decimal(v:Any, percent=False)->Decimal|None:
 if v in (None,""): return None
 is_numeric=isinstance(v,(int,float,Decimal)) and not isinstance(v,bool)
 if is_numeric: d=Decimal(str(v))
 else:
  s=re.sub(r"[^0-9,\.\-]","",str(v).replace(" ",""));
  if not s:return None
  if "," in s and "." in s: s=s.replace(".","").replace(",",".")
  else:s=s.replace(",",".")
  try:d=Decimal(s)
  except InvalidOperation: raise ValueError(f"Некорректное число: {v}")
 # Excel хранит 17% числом 0.17, а текстовые выгрузки — строкой «17%».
 # Масштабируем только числовую Excel-дробь и обязательно учитываем
 # модуль: прежняя проверка `d <= 1` ошибочно превращала -17% в -1700%.
 if percent and is_numeric and Decimal("0")<abs(d)<=Decimal("1"): d*=100
 return d
def parse_date(v:Any)->date:
 if isinstance(v,datetime):return v.date()
 if isinstance(v,date):return v
 if isinstance(v,(float,int)):return datetime(1899,12,30).date()+__import__('datetime').timedelta(days=int(v))
 s=str(v).strip()
 for f in ("%d.%m.%Y","%Y-%m-%d","%d/%m/%Y","%d.%m.%y"):
  try:return datetime.strptime(s.split()[0],f).date()
  except ValueError:pass
 raise ValueError(f"Некорректная дата: {v}")
def parse_bool(v:Any)->bool:return norm(v) in {"да","true","1","есть","социальная"}
def parse_phone(v:Any)->str|None:
 return normalize_phone(v)

def parse_items(text:Any)->list[dict]:
 raw=str(text or "").strip()
 if not raw:return []
 # В реальной выгрузке следующая позиция может начинаться после переноса строки
 # или сразу после разделителя `| Артикул:`.
 parts=[p.strip() for p in re.split(r"(?:[\n;]+|\|\s*(?=Артикул\s*:))",raw,flags=re.I) if p.strip()]
 result=[]
 # Поддерживаются строки с разделителями | и табуляцией; неизвестный формат сохраняется как название.
 for part in parts:
  fields=[x.strip() for x in re.split(r"\s*[|\t]\s*",part)]
  item={"article":None,"code":None,"name":part,"quantity":Decimal("1"),"base_price":None,"actual_price":None,"extra_data":part}
  if len(fields)>=3:
   labelled={}
   for field in fields:
    if ":" in field:
     label,value=field.split(":",1);label=compact(label);value=value.strip()
     for aliases,key in (({"артикул","арт"},"article"),({"код","кодтовара"},"code"),({"наименование","товар"},"name"),({"количество","колво"},"quantity"),({"ценабазовая","базоваяцена"},"base_price"),({"цена","ценапродажи"},"actual_price")):
      if label in aliases:labelled[key]=value;break
   if labelled:
    item.update(article=labelled.get("article") or None,code=labelled.get("code") or None,name=labelled.get("name") or part)
    for key in ("quantity","base_price","actual_price"):
     if labelled.get(key):item[key]=decimal(labelled[key])
   else:
    item.update(article=fields[0] or None,code=fields[1] or None,name=fields[2] or part)
    for key,pos in (("quantity",3),("base_price",4),("actual_price",5)):
     if len(fields)>pos and fields[pos]: item[key]=decimal(fields[pos])
  else:
   m=re.match(r"(?:(?P<article>\S+)\s+)?(?P<name>.+?)\s+[xх*]\s*(?P<qty>[\d,.]+)(?:\s+по\s+(?P<price>[\d,.]+))?$",part,re.I)
   if m:item.update(article=m["article"],name=m["name"],quantity=decimal(m["qty"]) or Decimal(1),actual_price=decimal(m["price"]))
  result.append(item)
 return result

def fingerprint(row:dict)->str:
 key="|".join([row["sale_date"].isoformat(),norm(row["document_number"]),norm(row["department"]),format(row["total_amount"],".2f")])
 return hashlib.sha256(key.encode()).hexdigest()

class _HtmlTables(HTMLParser):
 def __init__(self):super().__init__(convert_charrefs=True);self.tables=[];self.table=None;self.row=None;self.cell=None
 def handle_starttag(self,tag,attrs):
  tag=tag.lower().split(":")[-1]
  if tag=="table":self.table=[]
  elif tag in {"tr","row"} and self.table is not None:self.row=[]
  elif tag in {"td","th","cell","data"} and self.row is not None and self.cell is None:self.cell=[]
  elif tag=="br" and self.cell is not None:self.cell.append("\n")
 def handle_data(self,data):
  if self.cell is not None:self.cell.append(data)
 def handle_endtag(self,tag):
  tag=tag.lower().split(":")[-1]
  if tag in {"td","th","cell"} and self.cell is not None:
   self.row.append("".join(self.cell).strip());self.cell=None
  elif tag in {"tr","row"} and self.row is not None:
   if self.row:self.table.append(self.row)
   self.row=None
  elif tag=="table" and self.table is not None:
   if self.table:self.tables.append(self.table)
   self.table=None
 def finish(self):
  """Сохраняет последнюю таблицу даже у обрезанного HTML без закрывающих тегов."""
  if self.cell is not None and self.row is not None:
   self.row.append("".join(self.cell).strip());self.cell=None
  if self.row is not None and self.table is not None:
   if self.row:self.table.append(self.row)
   self.row=None
  if self.table is not None:
   if self.table:self.tables.append(self.table)
   self.table=None
def _decode_bytes(data:bytes,charset:str|None=None)->str:
 """Декодирует текст выгрузки, включая UTF без BOM и типичную кодировку 1С."""
 # Определяем UTF-32/UTF-16 без BOM по распределению NUL-байтов.
 if data.startswith((b"\xff\xfe\x00\x00",b"\x00\x00\xfe\xff")):encodings=["utf-32"]
 elif data.startswith((b"\xff\xfe",b"\xfe\xff")):encodings=["utf-16"]
 elif len(data)>=16 and data[1:400:2].count(0)>40:encodings=["utf-16-le"]
 elif len(data)>=16 and data[0:400:2].count(0)>40:encodings=["utf-16-be"]
 else:encodings=[]
 head=data[:4096].decode("ascii",errors="ignore")
 match=re.search(r"(?:charset|encoding)\s*=\s*['\"]?([\w-]+)",head,re.I)
 if charset:encodings.insert(0,charset)
 if match:encodings.append(match.group(1))
 encodings.extend(["utf-8-sig","windows-1251"])
 for encoding in encodings:
  try:return data.decode(encoding)
  except (LookupError,UnicodeDecodeError):continue
 return data.decode("utf-8",errors="replace")

def _decode_html(data:bytes)->list[str]:
 """Декодирует HTML/MHTML, даже если MIME-часть имеет ошибочный служебный тип."""
 plain=_decode_bytes(data)
 # Некоторые генераторы сохраняют MIME-заголовки в UTF-16. После первичного
 # декодирования переводим их в ASCII-совместимый вид для стандартного парсера.
 mime_data=data
 if re.search(r"(?im)^(?:mime-version|content-type):",plain[:8192]):
  mime_data=plain.encode("utf-8") if "\x00" in plain or data.startswith((b"\xff\xfe",b"\xfe\xff")) else data
  message=BytesParser(policy=policy.default).parsebytes(mime_data);documents=[]
  for part in message.walk():
   if part.is_multipart():continue
   payload=part.get_payload(decode=True)
   if payload is None:
    raw=part.get_payload()
    payload=raw.encode("utf-8") if isinstance(raw,str) else b""
   text=_decode_bytes(payload,part.get_content_charset())
   # В реальных выгрузках 1С HTML иногда ошибочно обозначен как
   # application/octet-stream, поэтому ориентируемся также на содержимое.
   if part.get_content_type()=="text/html" or re.search(r"<(?:\w+:)?(?:html|table|workbook)\b",text,re.I):
    documents.append(text)
  if documents:return documents
 documents=[plain]
 # Резерв для повреждённых MHTML без корректных MIME-заголовков.
 for decoded in (quopri.decodestring(data),):
  text=_decode_bytes(decoded)
  if text!=plain and re.search(r"<(?:\w+:)?table\b",text,re.I):documents.append(text)
 compact_data=re.sub(br"\s+",b"",data)
 try:
  decoded=base64.b64decode(compact_data,validate=True);text=_decode_bytes(decoded)
  if re.search(r"<(?:\w+:)?table\b",text,re.I):documents.append(text)
 except (ValueError,base64.binascii.Error):pass
 return documents

def _markup_text(value:str)->str:
 """Преобразует содержимое HTML-ячейки в обычный текст без потери переносов."""
 value=re.sub(r"<br\s*/?>", "\n",value,flags=re.I)
 value=re.sub(r"<[^>]+>","",value)
 return re.sub(r"[ \t\r\f\v]+"," ",unescape(value).replace("\xa0"," ")).strip()

def _fallback_html_rows(text:str)->list[list[str]]:
 """Извлекает строки из повреждённого HTML, который HTMLParser не собрал в таблицу."""
 rows=[]
 # Экспорты 1С встречаются без открывающего TABLE или с незакрытыми служебными
 # тегами. Для них достаточно восстановить пары TR/TD (либо Row/Cell из XML).
 for row_match in re.finditer(r"<(?:\w+:)?(?:tr|row)\b[^>]*>(.*?)(?=</(?:\w+:)?(?:tr|row)\s*>|<(?:\w+:)?(?:tr|row)\b|\Z)",text,re.I|re.S):
  body=row_match.group(1)
  cells=[_markup_text(match.group(1)) for match in re.finditer(r"<(?:\w+:)?(?:td|th|cell)\b[^>]*>(.*?)(?=</(?:\w+:)?(?:td|th|cell)\s*>|<(?:\w+:)?(?:td|th|cell)\b|\Z)",body,re.I|re.S)]
  if cells:rows.append(cells)
 if rows:return rows
 # Последний безопасный вариант — текстовая табличная выгрузка внутри PRE или
 # HTML-файл с неверным содержимым. Разделитель-табуляция не конфликтует с товарами.
 plain=re.sub(r"<br\s*/?>","\n",text,flags=re.I)
 plain=unescape(re.sub(r"<[^>]+>","",plain)).replace("\xa0"," ")
 return [[cell.strip() for cell in line.split("\t")] for line in plain.splitlines() if line.count("\t")>=3]

def _html_rows(path:Path)->list[list[list[str]]]:
 data=path.read_bytes()
 tables=[]
 for text in _decode_html(data):
  candidates=[text]
  # Бывают выгрузки, где весь документ HTML экранирован, либо таблица помещена
  # внутрь HTML-комментария. Браузер их показывает, но HTMLParser таблиц не видит.
  decoded=unescape(text)
  if decoded!=text:candidates.append(decoded)
  candidates.extend(comment for comment in re.findall(r"<!--(.*?)-->",text,re.S) if re.search(r"<table\b",comment,re.I))
  for candidate in candidates:
   parser=_HtmlTables();parser.feed(candidate);parser.close();parser.finish();tables.extend(parser.tables)
   if not parser.tables:
    recovered=_fallback_html_rows(candidate)
    if recovered:tables.append(recovered)
 return tables
def workbook_rows(path:Path)->Iterator[tuple[str,list[list[Any]]]]:
 with path.open("rb") as source:signature=source.read(8)
 if path.suffix.lower()==".xlsx" or signature.startswith(b"PK\x03\x04"):
  import openpyxl
  # Передаём поток: так XLSX корректно читается даже при ошибочном расширении .html.
  source=path.open("rb");wb=openpyxl.load_workbook(source,read_only=True,data_only=True)
  try:
   for ws in wb.worksheets:yield ws.title,[list(r) for r in ws.iter_rows(values_only=True)]
  finally:wb.close();source.close()
 elif path.suffix.lower()==".xls" or signature.startswith(b"\xd0\xcf\x11\xe0"):
  import xlrd
  wb=xlrd.open_workbook(path,on_demand=True)
  for ws in wb.sheets():yield ws.name,[ws.row_values(i) for i in range(ws.nrows)]
 elif path.suffix.lower() in {".html",".htm"}:
  for index,rows in enumerate(_html_rows(path),1):yield f"Таблица {index}",rows
 else:
  raise ValueError(f"Неподдерживаемый формат файла: {path.suffix or 'без расширения'}")
def read_sales(path:Path):
 diagnostics=[]
 for sheet,rows in workbook_rows(path):
  result=find_header(rows)
  if result is None:diagnostics.append(f"{sheet}: распознано 0 колонок");continue
  header_idx,mapping,best=result
  if header_idx<0:
   diagnostics.append(f"{sheet}: распознаны {', '.join(sorted(best))}; отсутствуют {', '.join(sorted(REQUIRED-best))}")
   continue
  output=[]
  for excel_row,values in enumerate(rows[header_idx+1:],header_idx+2):
   if not any(v not in (None,"") for v in values):continue
   raw={key:(values[i] if i<len(values) else None) for key,i in mapping.items()}
   output.append((excel_row,raw))
  return sheet,output
 details="; ".join(diagnostics) or "в книге нет доступных листов"
 raise ValueError(f"Не найдена строка заголовков с обязательными колонками. Проверены первые 1000 строк каждого листа. {details}")
def normalize_sale(raw:dict)->dict:
 gross_amount=decimal(raw.get("total_amount"));certificate_amount=decimal(raw.get("certificate_amount"))
 total_amount=gross_amount-certificate_amount if gross_amount is not None and certificate_amount is not None and certificate_amount>0 else gross_amount
 row={"row_number":int(raw["row_number"]) if raw.get("row_number") not in (None,"") else None,"sale_date":parse_date(raw.get("sale_date")),"document_number":str(raw.get("document_number") or "").strip(),"client":str(raw.get("client") or "").strip() or None,"department":str(raw.get("department") or "").strip(),"total_amount":total_amount,"base_amount":decimal(raw.get("base_amount")),"discount_percent":decimal(raw.get("discount_percent"),True),"reason":str(raw.get("reason") or "").strip() or None,"author":str(raw.get("author") or "").strip() or None,"price_type":str(raw.get("price_type") or "").strip() or None,"discount_card_percent":decimal(raw.get("discount_card_percent"),True),"discount_card_number":str(raw.get("discount_card_number") or "").split(".")[0].strip() or None,"social":parse_bool(raw.get("social")),"certificate_amount":certificate_amount,"promotion":str(raw.get("promotion") or "").strip() or None,"phone":parse_phone(raw.get("phone")),"original_products_text":str(raw.get("products") or "").strip() or None}
 if not row["document_number"] or not row["department"] or row["total_amount"] is None:raise ValueError("Не заполнены обязательные поля")
 row["fingerprint"]=fingerprint(row)
 if certificate_amount is not None and certificate_amount>0:
  legacy={**row,"total_amount":gross_amount};row["legacy_fingerprint"]=fingerprint(legacy)
 row["items"]=parse_items(raw.get("products"));return row
