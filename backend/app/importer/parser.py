import hashlib, json, re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator
import openpyxl, xlrd

ALIASES = {
 "row_number":["№ п/п","n п/п"], "sale_date":["дата"], "document_number":["№ док.","№ док","номер документа"],
 "client":["клиент"], "department":["подразделение"], "total_amount":["сумма"], "base_amount":["сумма базовых цен"],
 "discount_percent":["скидка %","скидка%"], "reason":["основание"], "author":["автор"], "price_type":["тип цены"],
 "discount_card_percent":["% дк","дк %"], "discount_card_number":["№ дк","номер дк"], "social":["социальная"],
 "certificate_amount":["сумма сертификатов"], "promotion":["акция"], "phone":["телефон"], "products":["товары"]}
REQUIRED={"sale_date","document_number","department","total_amount"}
def norm(v:Any)->str: return re.sub(r"\s+"," ",str(v or "").strip().lower().replace("ё","е"))
LOOKUP={norm(alias):key for key,aliases in ALIASES.items() for alias in aliases}
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
 parts=[p.strip() for p in re.split(r"[\n;]+",raw) if p.strip()]
 result=[]
 # Поддерживаются строки с разделителями | и табуляцией; неизвестный формат сохраняется как название.
 for part in parts:
  fields=[x.strip() for x in re.split(r"\s*[|\t]\s*",part)]
  item={"article":None,"code":None,"name":part,"quantity":Decimal("1"),"base_price":None,"actual_price":None,"extra_data":part}
  if len(fields)>=3:
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
  wb=openpyxl.load_workbook(path,read_only=True,data_only=True)
  for ws in wb.worksheets:yield ws.title,[list(r) for r in ws.iter_rows(values_only=True)]
 else:
  wb=xlrd.open_workbook(path,on_demand=True)
  for ws in wb.sheets():yield ws.name,[ws.row_values(i) for i in range(ws.nrows)]
def read_sales(path:Path):
 for sheet,rows in workbook_rows(path):
  header_idx=None; mapping={}
  for idx,row in enumerate(rows[:100]):
   found={LOOKUP[norm(v)]:i for i,v in enumerate(row) if norm(v) in LOOKUP}
   if REQUIRED.issubset(found):header_idx,mapping=idx,found;break
  if header_idx is None:continue
  output=[]
  for excel_row,values in enumerate(rows[header_idx+1:],header_idx+2):
   if not any(v not in (None,"") for v in values):continue
   raw={key:(values[i] if i<len(values) else None) for key,i in mapping.items()}
   output.append((excel_row,raw))
  return sheet,output
 raise ValueError("Не найдена строка заголовков с обязательными колонками")
def normalize_sale(raw:dict)->dict:
 row={"row_number":int(raw["row_number"]) if raw.get("row_number") not in (None,"") else None,"sale_date":parse_date(raw.get("sale_date")),"document_number":str(raw.get("document_number") or "").strip(),"client":str(raw.get("client") or "").strip() or None,"department":str(raw.get("department") or "").strip(),"total_amount":decimal(raw.get("total_amount")),"base_amount":decimal(raw.get("base_amount")),"discount_percent":decimal(raw.get("discount_percent"),True),"reason":str(raw.get("reason") or "").strip() or None,"author":str(raw.get("author") or "").strip() or None,"price_type":str(raw.get("price_type") or "").strip() or None,"discount_card_percent":decimal(raw.get("discount_card_percent"),True),"discount_card_number":str(raw.get("discount_card_number") or "").split(".")[0].strip() or None,"social":parse_bool(raw.get("social")),"certificate_amount":decimal(raw.get("certificate_amount")),"promotion":str(raw.get("promotion") or "").strip() or None,"phone":parse_phone(raw.get("phone")),"original_products_text":str(raw.get("products") or "").strip() or None}
 if not row["document_number"] or not row["department"] or row["total_amount"] is None:raise ValueError("Не заполнены обязательные поля")
 row["fingerprint"]=fingerprint(row);row["items"]=parse_items(raw.get("products"));return row
