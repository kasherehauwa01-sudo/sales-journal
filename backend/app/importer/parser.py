import hashlib, re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator
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
 if isinstance(v,(int,float,Decimal)): d=Decimal(str(v))
 else:
  s=re.sub(r"[^0-9,\.\-]","",str(v).replace(" ",""));
  if not s:return None
  if "," in s and "." in s: s=s.replace(".","").replace(",",".")
  else:s=s.replace(",",".")
  try:d=Decimal(s)
  except InvalidOperation: raise ValueError(f"Некорректное число: {v}")
 if percent and d<=1 and d!=0: d*=100
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
 if v in (None,""):return None
 digits=re.sub(r"\D","",str(v).split(".")[0]); return ("+7"+digits[-10:]) if len(digits)>=10 else digits or None

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

def workbook_rows(path:Path)->Iterator[tuple[str,list[list[Any]]]]:
 if path.suffix.lower()==".xlsx":
  import openpyxl
  wb=openpyxl.load_workbook(path,read_only=True,data_only=True)
  for ws in wb.worksheets:yield ws.title,[list(r) for r in ws.iter_rows(values_only=True)]
 else:
  import xlrd
  wb=xlrd.open_workbook(path,on_demand=True)
  for ws in wb.sheets():yield ws.name,[ws.row_values(i) for i in range(ws.nrows)]
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
 row={"row_number":int(raw["row_number"]) if raw.get("row_number") not in (None,"") else None,"sale_date":parse_date(raw.get("sale_date")),"document_number":str(raw.get("document_number") or "").strip(),"client":str(raw.get("client") or "").strip() or None,"department":str(raw.get("department") or "").strip(),"total_amount":decimal(raw.get("total_amount")),"base_amount":decimal(raw.get("base_amount")),"discount_percent":decimal(raw.get("discount_percent"),True),"reason":str(raw.get("reason") or "").strip() or None,"author":str(raw.get("author") or "").strip() or None,"price_type":str(raw.get("price_type") or "").strip() or None,"discount_card_percent":decimal(raw.get("discount_card_percent"),True),"discount_card_number":str(raw.get("discount_card_number") or "").split(".")[0].strip() or None,"social":parse_bool(raw.get("social")),"certificate_amount":decimal(raw.get("certificate_amount")),"promotion":str(raw.get("promotion") or "").strip() or None,"phone":parse_phone(raw.get("phone")),"original_products_text":str(raw.get("products") or "").strip() or None}
 if not row["document_number"] or not row["department"] or row["total_amount"] is None:raise ValueError("Не заполнены обязательные поля")
 row["fingerprint"]=fingerprint(row);row["items"]=parse_items(raw.get("products"));return row
