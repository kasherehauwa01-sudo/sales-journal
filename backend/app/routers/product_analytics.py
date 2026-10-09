from datetime import date
from io import BytesIO
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, any_, bindparam, case, cast, distinct, false, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Sale, SaleItem
from app.services.clients_vr import ClientsVrError, get_client_managers, get_managers
from app.services.product_analytics import GROUP_FIELDS, MISSING_MANAGER, classify, clients_for_managers, filter_subcategories, filter_values, group_rows, merge_periods, previous_period, product_key, summary
from app.services.product_analytics_cache import cached_product_dataset
from app.services.vrcatalog import VrCatalogError, get_catalog_batch_info, get_product_filter_options, get_product_filters

router=APIRouter(prefix="/reports/product-analytics",tags=["Отчеты"])
logger=logging.getLogger(__name__)

class AnalyticsRequest(BaseModel):
 date_from:date;date_to:date;compare_from:date|None=None;compare_to:date|None=None;departments:list[str]=Field(default_factory=list);managers:list[str]=Field(default_factory=list);brands:list[str]=Field(default_factory=list);manufacturers:list[str]=Field(default_factory=list);subcategories:list[str]=Field(default_factory=list);group_by:str="product";article:str|None=None;search:str|None=None

def _dates(data):
 if data.date_from>data.date_to:raise HTTPException(422,"Дата начала не может быть позже даты окончания")
 if bool(data.compare_from)!=bool(data.compare_to):raise HTTPException(422,"Укажите обе даты периода сравнения")
 return (data.compare_from,data.compare_to) if data.compare_from else previous_period(data.date_from,data.date_to)

def _conditions(data,start,end,manager_clients=None):
 result=[Sale.sale_date>=start,Sale.sale_date<=end]
 if data.departments:result.append(Sale.department.in_(data.departments))
 if manager_clients is not None:
  if manager_clients:
   normalized=func.coalesce(func.lower(func.trim(Sale.client)),"")
   clients=cast(bindparam("manager_clients",value=manager_clients,unique=True),ARRAY(String))
   result.append(normalized==any_(clients))
  else:result.append(false())
 if data.article:result.append(or_(SaleItem.article.ilike(f"%{data.article.strip()}%"),SaleItem.code.ilike(f"%{data.article.strip()}%")))
 if data.search:result.append(SaleItem.name.ilike(f"%{data.search.strip()}%"))
 return result

async def _period_rows(data,db,start,end,manager_clients):
 revenue=func.coalesce(func.sum(SaleItem.quantity*SaleItem.actual_price),0);units=func.coalesce(func.sum(SaleItem.quantity),0)
 fallback_name=case((and_(or_(SaleItem.article.is_(None),func.trim(SaleItem.article)==""),or_(SaleItem.code.is_(None),func.trim(SaleItem.code)=="")),func.lower(func.trim(SaleItem.name))),else_="")
 q=select(SaleItem.article,SaleItem.code,func.max(SaleItem.name).label("name"),revenue.label("revenue"),units.label("units"),func.count(distinct(Sale.id)).label("checks"),func.min(Sale.sale_date).label("first_sale"),func.max(Sale.sale_date).label("last_sale")).join(Sale,Sale.id==SaleItem.sale_id).where(*_conditions(data,start,end,manager_clients)).group_by(SaleItem.article,SaleItem.code,fallback_name)
 rows=(await db.execute(q)).all();return [{"key":product_key(x.article,x.code,x.name),"article":x.article,"code":x.code,"name":x.name,"revenue":float(x.revenue),"units":float(x.units),"checks":x.checks,"first_sale":x.first_sale,"last_sale":x.last_sale} for x in rows]

async def _catalog(rows):
 products=[{"article":x.get("article"),"code":x.get("code")} for x in rows if x.get("article") or x.get("code")];result={}
 try:
  result.update(await get_catalog_batch_info(products))
 except VrCatalogError as exc:
  # Продажи остаются доступны, но причина отсутствия enrichment видна в логах.
  logger.warning("CatalogVR enrichment недоступен для %s строк: %s",len(products),type(exc).__name__)
 return result

async def _manager_clients(data,db,old_from,old_to):
 if not data.managers:return None
 try:mapping=await get_client_managers()
 except ClientsVrError as exc:raise HTTPException(502,str(exc)) from exc
 normalized=func.coalesce(func.lower(func.trim(Sale.client)),"")
 scope=or_(and_(Sale.sale_date>=data.date_from,Sale.sale_date<=data.date_to),and_(Sale.sale_date>=old_from,Sale.sale_date<=old_to))
 conditions=[scope]
 if data.departments:conditions.append(Sale.department.in_(data.departments))
 sales_clients=list((await db.scalars(select(normalized).where(*conditions).distinct())).all())
 return clients_for_managers(sales_clients,mapping,data.managers)

def _dataset_cache_key(data:AnalyticsRequest,revision:int) -> str:
 payload=data.model_dump(mode="json")
 for field in ("departments","managers","brands","manufacturers","subcategories"):
  payload[field]=sorted(payload[field],key=str.casefold)
 return f"{revision}:"+json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))

async def _build_dataset(data,db):
 if data.group_by not in GROUP_FIELDS:raise HTTPException(422,"Неизвестная группировка")
 started=time.perf_counter();old_from,old_to=_dates(data);manager_clients=await _manager_clients(data,db,old_from,old_to)
 stage=time.perf_counter();current=await _period_rows(data,db,data.date_from,data.date_to,manager_clients);current_ms=(time.perf_counter()-stage)*1000
 stage=time.perf_counter();old=await _period_rows(data,db,old_from,old_to,manager_clients);comparison_ms=(time.perf_counter()-stage)*1000
 stage=time.perf_counter();catalog=await _catalog(current+old);catalog_ms=(time.perf_counter()-stage)*1000
 stage=time.perf_counter();rows=merge_periods(current,old,catalog)
 rows=filter_values(rows,"brand",data.brands)
 rows=filter_values(rows,"manufacturer",data.manufacturers)
 rows=filter_subcategories(rows,data.subcategories)
 rows=group_rows(rows,data.group_by);processing_ms=(time.perf_counter()-stage)*1000
 logger.info("product_analytics stages_ms current=%.1f comparison=%.1f catalog=%.1f processing=%.1f total=%.1f current_rows=%s comparison_rows=%s result_rows=%s",
  current_ms,comparison_ms,catalog_ms,processing_ms,(time.perf_counter()-started)*1000,len(current),len(old),len(rows))
 return rows,old_from,old_to,manager_clients

async def _dataset(data,db):
 # max(id) использует PK-индекс и инвалидирует кеш между worker-процессами.
 revision=int(await db.scalar(select(func.max(Sale.id))) or 0)
 key=_dataset_cache_key(data,revision)
 result,hit=await cached_product_dataset(key,lambda:_build_dataset(data,db))
 logger.info("product_analytics dataset cache_hit=%s result_rows=%s",hit,len(result[0]))
 return result

def _sorted(rows,section,sort_by="revenue",limit=100):
 groups=classify(rows) if section!="top" else {}
 source=rows if section=="top" else groups[section]
 if section=="growth":key="revenue_difference" if sort_by!="percent" else "revenue_change";reverse=True
 elif section=="decline":key="revenue_difference";reverse=False
 elif section=="stopped":key="previous_revenue";reverse=True
 else:key=sort_by if sort_by in {"revenue","units","checks"} else "revenue";reverse=True
 return sorted(source,key=lambda x:x.get(key) if x.get(key) is not None else float("-inf"),reverse=reverse)[:limit]

def _selected_products_condition(rows,metric):
 values={"code":[],"article":[],"unknown":[]}
 for row in rows:
  if not row.get(metric):continue
  prefix,_,value=row["key"].partition(":")
  if prefix in values and value:values[prefix].append(value)
 conditions=[]
 for prefix,column in (("code",SaleItem.code),("article",SaleItem.article),("unknown",SaleItem.name)):
  if values[prefix]:
   selected=cast(bindparam(f"selected_{prefix}_{metric}",value=values[prefix],unique=True),ARRAY(String))
   conditions.append(func.lower(func.trim(column))==any_(selected))
 return or_(*conditions) if conditions else false()

async def _unique_checks(data,db,rows,start,end,manager_clients,metric):
 """Считает каждый документ один раз, даже если в нём несколько отобранных SKU."""
 return await db.scalar(select(func.count(distinct(Sale.id))).join(SaleItem,SaleItem.sale_id==Sale.id).where(
  *_conditions(data,start,end,manager_clients),_selected_products_condition(rows,metric))) or 0

def _source(payload):
 if isinstance(payload,list):return payload
 if isinstance(payload,dict):
  for key in ("items","options","values","filters","data","results"):
   value=payload.get(key)
   if isinstance(value,list):return value
   if isinstance(value,dict):
    nested=_source(value)
    if nested:return nested
 return []

def _option_values(payload):
 result=[]
 for item in _source(payload):
  value=item if isinstance(item,str) else next((item.get(key) for key in ("value","label","name","title","id") if item.get(key) is not None),None) if isinstance(item,dict) else None
  if value is not None and str(value).strip():result.append(str(value).strip())
 return list(dict.fromkeys(result))

@router.get("/manager-options")
async def manager_options(db:AsyncSession=Depends(get_db)):
 managers=[];mapping={};last_error=None
 try:managers=await get_managers()
 except ClientsVrError as exc:last_error=exc
 try:mapping=await get_client_managers()
 except ClientsVrError as exc:last_error=exc
 if not managers:managers=sorted(set(mapping.values()),key=str.casefold)
 if not managers and not mapping:raise HTTPException(502,str(last_error or "ClientsVR недоступен"))
 clients=list((await db.scalars(select(func.coalesce(func.lower(func.trim(Sale.client)),"")).distinct())).all())
 managers=[value for value in managers if value.strip().casefold() not in {"нет менеджера",MISSING_MANAGER.casefold()}]
 if any(not mapping.get(client or "") or mapping[client or ""].strip().casefold() in {"нет менеджера",MISSING_MANAGER.casefold()} for client in clients):managers=[*managers,MISSING_MANAGER]
 return list(dict.fromkeys(managers))

async def _catalog_options(field:str,search:str):
 aliases={"brand":["brand","Бренд","property:Бренд"],"manufacturer":["manufacturer","Производитель","property:Производитель"],"subcategory":["section","Раздел","property:Раздел"]}[field];keys=[]
 try:
  for item in _source(await get_product_filters()):
   if not isinstance(item,dict):continue
   label=str(item.get("label") or item.get("name") or item.get("title") or "").casefold();candidate=str(item.get("key") or item.get("code") or item.get("id") or "")
   terms={"brand":("brand","бренд"),"manufacturer":("manufacturer","производител"),"subcategory":("section","раздел")}[field]
   if any(term in candidate.casefold() or term in label for term in terms):keys.append(candidate)
 except VrCatalogError:pass
 last=None
 for key in dict.fromkeys([*keys,*aliases]):
  if not key:continue
  try:
   values=_option_values(await get_product_filter_options(key,search=search,page=1,page_size=500))
   if values:return values
  except VrCatalogError as exc:last=exc
 if last:raise HTTPException(502,str(last))
 return []

@router.get("/catalog-options/{field}")
async def catalog_options(field:str,search:str=""):
 if field not in {"brand","manufacturer","subcategory"}:raise HTTPException(404,"Неизвестный фильтр")
 return await _catalog_options(field,search)

@router.get("/name-suggestions")
async def name_suggestions(search:str=Query(min_length=4,max_length=200),db:AsyncSession=Depends(get_db)):
 value=search.strip()
 if len(value)<4:return []
 q=select(SaleItem.name).where(SaleItem.name.ilike(f"%{value}%")).distinct().order_by(SaleItem.name).limit(20)
 return list((await db.scalars(q)).all())

@router.post("/summary")
async def report_summary(data:AnalyticsRequest,db:AsyncSession=Depends(get_db)):
 # KPI всегда считаются по SKU, независимо от выбранной группировки таблицы.
 product_data=data.model_copy(update={"group_by":"product"});rows,old_from,old_to,manager_clients=await _dataset(product_data,db);result=summary(rows)
 # Без фильтров CatalogVR можно получить реальное число уникальных чеков
 # напрямую в PostgreSQL, не суммируя чеки отдельных SKU.
 if data.brands or data.manufacturers or data.subcategories:
  current_checks=await _unique_checks(data,db,rows,data.date_from,data.date_to,manager_clients,"revenue")
  old_checks=await _unique_checks(data,db,rows,old_from,old_to,manager_clients,"previous_revenue")
 else:
  current_checks=await db.scalar(select(func.count(distinct(Sale.id))).join(SaleItem,SaleItem.sale_id==Sale.id).where(*_conditions(data,data.date_from,data.date_to,manager_clients))) or 0
  old_checks=await db.scalar(select(func.count(distinct(Sale.id))).join(SaleItem,SaleItem.sale_id==Sale.id).where(*_conditions(data,old_from,old_to,manager_clients))) or 0
 result["checks"]={"current":current_checks,"previous":old_checks}
 return {"period":{"start":data.date_from,"end":data.date_to},"comparison":{"start":old_from,"end":old_to},"summary":result}

@router.post("/report")
async def full_report(data:AnalyticsRequest,section:str=Query("top",pattern="^(top|growth|decline|stopped|new)$"),
                      sort_by:str="revenue",limit:int=Query(20,ge=10,le=500),db:AsyncSession=Depends(get_db)):
 """Возвращает KPI и активную вкладку из одного рассчитанного набора."""
 if data.group_by not in GROUP_FIELDS:raise HTTPException(422,"Неизвестная группировка")
 product_data=data.model_copy(update={"group_by":"product"})
 product_rows,old_from,old_to,manager_clients=await _dataset(product_data,db)
 grouped=product_rows if data.group_by=="product" else group_rows(product_rows,data.group_by)
 result=summary(product_rows)
 # Уникальные чеки не суммируем между SKU. Без catalog-фильтров считаем их точно в SQL.
 if data.brands or data.manufacturers or data.subcategories:
  result["checks"]={
   "current":await _unique_checks(data,db,product_rows,data.date_from,data.date_to,manager_clients,"revenue"),
   "previous":await _unique_checks(data,db,product_rows,old_from,old_to,manager_clients,"previous_revenue"),
  }
 else:
  result["checks"]={
   "current":await db.scalar(select(func.count(distinct(Sale.id))).join(SaleItem,SaleItem.sale_id==Sale.id).where(*_conditions(data,data.date_from,data.date_to,manager_clients))) or 0,
   "previous":await db.scalar(select(func.count(distinct(Sale.id))).join(SaleItem,SaleItem.sale_id==Sale.id).where(*_conditions(data,old_from,old_to,manager_clients))) or 0,
  }
 items=_sorted(grouped,section,sort_by,limit)
 total=len(_sorted(grouped,section,sort_by,1000000))
 return {"period":{"start":data.date_from,"end":data.date_to},"comparison":{"start":old_from,"end":old_to},
         "summary":result,"items":items,"total":total}

async def _section(data,db,name,sort_by,limit):rows,_,_,_=await _dataset(data,db);return {"items":_sorted(rows,name,sort_by,limit),"total":len(_sorted(rows,name,sort_by,1000000))}
@router.post("/top")
async def top(data:AnalyticsRequest,sort_by:str="revenue",limit:int=Query(20,ge=10,le=100),db:AsyncSession=Depends(get_db)):return await _section(data,db,"top",sort_by,limit)
@router.post("/growth")
async def growth(data:AnalyticsRequest,sort_by:str="absolute",limit:int=Query(100,le=500),db:AsyncSession=Depends(get_db)):return await _section(data,db,"growth",sort_by,limit)
@router.post("/decline")
async def decline(data:AnalyticsRequest,limit:int=Query(100,le=500),db:AsyncSession=Depends(get_db)):return await _section(data,db,"decline","absolute",limit)
@router.post("/stopped")
async def stopped(data:AnalyticsRequest,limit:int=Query(100,le=500),db:AsyncSession=Depends(get_db)):return await _section(data,db,"stopped","revenue",limit)
@router.post("/new")
async def new(data:AnalyticsRequest,limit:int=Query(100,le=500),db:AsyncSession=Depends(get_db)):return await _section(data,db,"new","revenue",limit)

class DetailRequest(AnalyticsRequest):article_key:str

def _detail_product(data:DetailRequest):
 """Строит точное условие для одного SKU из ключа таблицы."""
 try:prefix,value=data.article_key.split(":",1)
 except ValueError as exc:raise HTTPException(422,"Неверный ключ товара") from exc
 if prefix=="code":column=SaleItem.code
 elif prefix=="article":column=SaleItem.article
 elif prefix=="unknown":column=SaleItem.name
 else:raise HTTPException(422,"Неверный ключ товара")
 return func.lower(func.trim(column))==value.strip().casefold(),prefix,value.strip()

async def _detail_manager_clients(data:DetailRequest,db:AsyncSession,product_condition):
 """Определяет клиентов менеджеров только для выбранного SKU и периода."""
 if not data.managers:return None
 try:mapping=await get_client_managers()
 except ClientsVrError as exc:raise HTTPException(502,str(exc)) from exc
 normalized=func.coalesce(func.lower(func.trim(Sale.client)),"")
 conditions=[*_conditions(data,data.date_from,data.date_to),product_condition]
 clients=list((await db.scalars(select(normalized).join(SaleItem,SaleItem.sale_id==Sale.id).where(*conditions).distinct())).all())
 return clients_for_managers(clients,mapping,data.managers)

def _empty_detail_product(article_key:str,prefix:str,value:str):
 """Сохраняет структуру product, даже если в периоде нет продаж."""
 return {"key":article_key,"article":value if prefix=="article" else None,
         "code":value if prefix=="code" else None,"name":value if prefix=="unknown" else "Товар",
         "revenue":0.0,"units":0.0,"checks":0,"first_sale":None,"last_sale":None}

@router.post("/details")
async def details(data:DetailRequest,group_by:str=Query("day",pattern="^(day|week|month)$"),db:AsyncSession=Depends(get_db)):
 if data.date_from>data.date_to:raise HTTPException(422,"Дата начала не может быть позже даты окончания")
 condition,prefix,value=_detail_product(data)
 manager_clients=await _detail_manager_clients(data,db,condition)
 bucket=func.date_trunc(group_by,Sale.sale_date).label("period")
 query=select(
  bucket,func.sum(SaleItem.quantity*SaleItem.actual_price).label("revenue"),
  func.sum(SaleItem.quantity).label("units"),func.count(distinct(Sale.id)).label("checks"),
  func.max(SaleItem.article).label("article"),func.max(SaleItem.code).label("code"),
  func.max(SaleItem.name).label("name"),func.min(Sale.sale_date).label("first_sale"),
  func.max(Sale.sale_date).label("last_sale"),
 ).join(Sale,Sale.id==SaleItem.sale_id).where(
  *_conditions(data,data.date_from,data.date_to,manager_clients),condition
 ).group_by(bucket).order_by(bucket)
 rows=(await db.execute(query)).all()
 points=[{"period":row.period.date(),"revenue":float(row.revenue or 0),
          "units":float(row.units or 0),"checks":row.checks} for row in rows]
 if rows:
  first=rows[0]
  current={"key":data.article_key,"article":first.article,"code":first.code,"name":first.name,
           "revenue":sum(point["revenue"] for point in points),"units":sum(point["units"] for point in points),
           "checks":sum(point["checks"] for point in points),"first_sale":min(row.first_sale for row in rows),
           "last_sale":max(row.last_sale for row in rows)}
 else:
  identity=(await db.execute(select(SaleItem.article,SaleItem.code,SaleItem.name).where(
   condition).order_by(SaleItem.id.desc()).limit(1))).first()
  current=_empty_detail_product(data.article_key,prefix,value)
  if identity:current.update(article=identity.article,code=identity.code,name=identity.name)
 catalog=await _catalog([current])
 product=merge_periods([current],[],catalog)[0]
 return {"product":product,"group_by":group_by,"points":points}

def _sheet(book,title,headers,rows):
 sheet=book.create_sheet(title);sheet.append(headers)
 for row in rows:sheet.append(row)
 sheet.freeze_panes="A2"
 for i,column in enumerate(sheet.columns,1):sheet.column_dimensions[get_column_letter(i)].width=min(45,max(12,max(len(str(x.value or "")) for x in column)+2))

@router.post("/export")
async def export(data:AnalyticsRequest,db:AsyncSession=Depends(get_db)):
 rows,old_from,old_to,_=await _dataset(data,db);sections={"ТОП":_sorted(rows,"top","revenue",1000000),"Рост":_sorted(rows,"growth","absolute",1000000),"Падение":_sorted(rows,"decline","absolute",1000000),"Перестали продаваться":_sorted(rows,"stopped","revenue",1000000),"Новые товары":_sorted(rows,"new","revenue",1000000)};book=Workbook();book.remove(book.active);s=summary(rows);_sheet(book,"Сводка",["Показатель","Значение"],[[k,str(v)] for k,v in s.items()]+[["Основной период",f"{data.date_from} — {data.date_to}"],["Период сравнения",f"{old_from} — {old_to}"]]);headers=["Артикул","Код","Название","Бренд","Категория","Выручка","Прошлая выручка","Изменение","Изменение, %","Количество","Прошлое количество","Чеков"]
 for title,items in sections.items():_sheet(book,title,headers,[[x.get("article"),x.get("code"),x.get("name"),x.get("brand"),x.get("category"),x.get("revenue"),x.get("previous_revenue"),x.get("revenue_difference"),x.get("revenue_change"),x.get("units"),x.get("previous_units"),x.get("checks")] for x in items]);output=BytesIO();book.save(output);return Response(output.getvalue(),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":"attachment; filename=product-analytics.xlsx"})
