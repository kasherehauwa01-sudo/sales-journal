from datetime import date, timedelta
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Response
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import case, distinct, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Sale, SaleItem
from app.services.clients_vr import ClientsVrError
from app.services.product_analytics import catalog_property, product_key
from app.services.promotion_candidates import NOT_FOUND, TABLE_ROW_LIMIT, UNCATEGORIZED, classify_candidate, percent_change, table_rows
from app.services.sales_client_filters import get_sales_filter_clients
from app.services.vrcatalog import VrCatalogError, get_catalog_batch_info

router=APIRouter(prefix="/reports/promotion-candidates",tags=["Отчеты"])

class Criteria(BaseModel):
 decline_percent:float=-30;min_previous_units:float=5;stale_days:int=30;low_sales_units:float=2;excess_stock_months:float=6;min_stock:float=1;recent_promotion_months:int=3

class Request(BaseModel):
 date_from:date;date_to:date;departments:list[str]=Field(default_factory=list);manager:str|None=None;buyer_type:str|None=None;category:str|None=None;brand:str|None=None;manufacturer:str|None=None;reason:str|None=None;status:str|None=None;criteria:Criteria=Field(default_factory=Criteria)

def _conditions(data,clients):
 conditions=[Sale.sale_date>=data.date_from,Sale.sale_date<=data.date_to]
 if data.departments:conditions.append(Sale.department.in_(data.departments))
 if clients is not None:
  names=[x.strip().casefold() for x in clients if x.strip()];conditions.append(func.lower(func.trim(Sale.client)).in_(names) if names else false())
 return conditions

async def _sales(data,db,clients):
 end=data.date_to;last=end-timedelta(days=29);previous_start=end-timedelta(days=59);previous_end=end-timedelta(days=30);old_start=end-timedelta(days=89);old_end=end-timedelta(days=60)
 units=func.coalesce(func.sum(SaleItem.quantity),0);revenue=func.coalesce(func.sum(SaleItem.quantity*SaleItem.actual_price),0)
 def conditional(value,start,finish):return func.coalesce(func.sum(case((Sale.sale_date.between(start,finish),value),else_=0)),0)
 rows=(await db.execute(select(
  SaleItem.code,SaleItem.article,func.max(SaleItem.name).label("name"),units.label("units"),revenue.label("revenue"),func.count(distinct(Sale.id)).label("checks"),
  conditional(SaleItem.quantity,last,end).label("units_30"),conditional(SaleItem.quantity,previous_start,previous_end).label("units_previous_30"),conditional(SaleItem.quantity,old_start,old_end).label("units_60_90"),
  conditional(SaleItem.quantity*SaleItem.actual_price,last,end).label("revenue_30"),conditional(SaleItem.quantity*SaleItem.actual_price,previous_start,previous_end).label("revenue_previous_30"),
  func.min(Sale.sale_date).label("first_sale"),func.max(Sale.sale_date).label("last_sale"),func.count(distinct(Sale.department)).label("stores"),
 ).join(Sale).where(*_conditions(data,clients)).group_by(SaleItem.code,SaleItem.article))).all()
 return [{"key":product_key(x.article,x.code,x.name),"code":x.code,"article":x.article,"name":x.name,"units":float(x.units),"revenue":float(x.revenue),"checks":x.checks,"average_price":float(x.revenue)/float(x.units) if x.units else 0,"units_30":float(x.units_30),"units_previous_30":float(x.units_previous_30),"units_60_90":float(x.units_60_90),"revenue_30":float(x.revenue_30),"revenue_previous_30":float(x.revenue_previous_30),"first_sale":x.first_sale,"last_sale":x.last_sale,"stores":x.stores,"analysis_days":(data.date_to-x.first_sale).days+1 if x.first_sale else 0} for x in rows]

def _stock(info):
 values=info.get("stocks") or info.get("warehouse_stocks") or info.get("balances") or []
 if isinstance(values,dict):values=list(values.values())
 result=0.0;found=False
 for item in values if isinstance(values,list) else []:
  raw=item.get("quantity",item.get("stock_quantity",item.get("value"))) if isinstance(item,dict) else item
  try:result+=float(raw);found=True
  except (TypeError,ValueError):pass
 return result if found else None

async def _catalog(rows):
 products=[{"code":x["code"],"article":x["article"]} for x in rows if x["code"] or x["article"]];result={}
 try:
  for offset in range(0,len(products),5000):result.update(await get_catalog_batch_info(products[offset:offset+5000]))
 except VrCatalogError:return {},False
 return result,True

async def _dataset(data,db):
 if data.date_from>data.date_to:raise HTTPException(422,"Дата начала не может быть позже даты окончания")
 try:clients=await get_sales_filter_clients(db,data.manager,data.buyer_type)
 except ClientsVrError as exc:raise HTTPException(502,str(exc)) from exc
 rows=await _sales(data,db,clients);catalog,available=await _catalog(rows)
 for row in rows:
  info=catalog.get(row["key"]);row.update(category=catalog_property(info,"category") if info else NOT_FOUND if available else "CatalogVR недоступен",brand=catalog_property(info,"brand") if info else "Не заполнено",manufacturer=catalog_property(info,"manufacturer") if info else "Не заполнено",stock=_stock(info or {}))
  if row["category"]=="Не заполнено":row["category"]=UNCATEGORIZED
 category_totals={}
 for row in rows:
  target=category_totals.setdefault(row["category"],[0,0]);target[0]+=row["units_30"];target[1]+=row["units_previous_30"]
 for row in rows:
  current,previous=category_totals[row["category"]];row["category_change_percent"]=percent_change(current,previous);classify_candidate(row,data.criteria.model_dump(),data.date_to)
  row["stock_value"]=row["stock"]*row["average_price"] if row["stock"] is not None and row["average_price"] else None
 filtered=[x for x in rows if (not data.category or x["category"].casefold()==data.category.casefold()) and (not data.brand or x["brand"].casefold()==data.brand.casefold()) and (not data.manufacturer or x["manufacturer"].casefold()==data.manufacturer.casefold()) and (not data.reason or data.reason in x["reasons"]) and (not data.status or x["status"]==data.status)]
 return sorted(filtered,key=lambda x:(len(x["reasons"]),x["stock_value"] or 0),reverse=True),available

@router.post("")
async def report(data:Request,db:AsyncSession=Depends(get_db)):
 rows,available=await _dataset(data,db);return {"items":table_rows(rows),"total":len(rows),"table_limit":TABLE_ROW_LIMIT,"summary":{"candidates":sum(x["status"]=="Кандидат" for x in rows),"stale":sum("Давно не продавался" in x["reasons"] for x in rows),"excess":sum("Избыточный запас" in x["reasons"] for x in rows),"decline":sum("Продажи падают" in x["reasons"] for x in rows)},"catalog_available":available,"limitations":["Историческое поле «Вид товара» не хранится в Sale/SaleItem; эффективность прошлых акций недоступна без изменения импорта." ]}

@router.post("/history")
async def history(_data:Request):return {"items":[],"available":False,"message":"История недоступна: «Вид товара» не сохраняется в строках продаж. Текущее свойство CatalogVR не позволяет восстановить исторические периоды акции."}

@router.post("/export")
async def export(data:Request,db:AsyncSession=Depends(get_db)):
 rows,_=await _dataset(data,db);book=Workbook();sheet=book.active;sheet.title="Кандидаты";sheet.append(["Товар","Код","Артикул","Категория","Бренд","Причины","Статус","Выручка","Продано","Продажи 30","Предыдущие 30","Изменение, %","Категория, %","Остаток","Запас, мес.","Дней без продажи"])
 for x in rows:sheet.append([x["name"],x["code"],x["article"],x["category"],x["brand"],", ".join(x["reasons"]),x["status"],x["revenue"],x["units"],x["units_30"],x["units_previous_30"],x["units_change_percent"],x["category_change_percent"],x["stock"],x["stock_months"],x["days_without_sales"]])
 history=book.create_sheet("История акций");history.append(["История недоступна","Поле «Вид товара» не сохраняется в SaleItem"]);output=BytesIO();book.save(output);return Response(output.getvalue(),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":"attachment; filename=promotion-candidates.xlsx"})
