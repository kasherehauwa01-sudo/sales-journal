from datetime import date
from io import BytesIO
import json
import logging
import time
from itertools import chain
from functools import wraps
import sys
from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse
from app.services.product_analytics_runtime import heavy_operation, run_cpu, diagnostics, ensure_headroom

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, any_, bindparam, case, cast, distinct, false, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_db
from app.config import settings
from app.models import Sale, SaleItem
from app.services.clients_vr import ClientsVrError, get_client_managers, get_managers
from app.services.product_analytics import GROUP_FIELDS, MISSING_MANAGER, classify, clients_for_managers, filter_subcategories, filter_values, group_rows, merge_periods, previous_period, product_key, summary
from app.services.product_analytics_cache import cached_product_dataset
from app.services.catalog_attributes import local_catalog, begin_catalog_snapshot, catalog_revision, local_options, coverage, sync_status, remember_detail_image
from app.services.vrcatalog import VrCatalogError, get_catalog_batch_info, get_product_filter_options, get_product_filters

class HeavyAnalyticsRoute(APIRoute):
 def __init__(self,path,endpoint,**kwargs):
  if "POST" in (kwargs.get("methods") or ()) and not getattr(endpoint,"_analytics_serialized",False):
   original=endpoint
   @wraps(original)
   async def serialized(*args,**kw):
    result=await original(*args,**kw)
    if isinstance(result,Response):return result
    def encode():return JSONResponse(jsonable_encoder(result))
    return await run_cpu(encode)
   serialized._analytics_serialized=True
   endpoint=serialized
  super().__init__(path,endpoint,**kwargs)
 async def handle(self,scope,receive,send):
  if scope["method"]!="POST":return await super().handle(scope,receive,send)
  try:
   async with heavy_operation("response"):
    await super().handle(scope,receive,send)
  except HTTPException as exc:
   if exc.status_code!=503:raise
   await JSONResponse({"detail":exc.detail},status_code=503,headers=exc.headers)(scope,receive,send)

router=APIRouter(route_class=HeavyAnalyticsRoute,prefix="/reports/product-analytics",tags=["Отчеты"])
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
 rows=[]
 result=await db.stream(q.execution_options(yield_per=512))
 try:
  async for x in result:
   if len(rows)%512==0:ensure_headroom()
   rows.append({"key":product_key(x.article,x.code,x.name),"article":x.article,"code":x.code,"name":x.name,"revenue":float(x.revenue),"units":float(x.units),"checks":x.checks,"first_sale":x.first_sale,"last_sale":x.last_sale})
 finally:await result.close()
 return rows

async def _catalog(rows):
 products=({"article":x.get("article"),"code":x.get("code")} for x in rows if x.get("article") or x.get("code"))
 try:return await get_catalog_batch_info(products)
 except VrCatalogError as exc:
  logger.warning("CatalogVR enrichment недоступен: %s",type(exc).__name__)
  return {}

async def _manager_clients(data,db,old_from,old_to):
 if not data.managers:return None
 try:mapping=await get_client_managers()
 except ClientsVrError as exc:raise HTTPException(502,str(exc)) from exc
 normalized=func.coalesce(func.lower(func.trim(Sale.client)),"")
 scope=or_(and_(Sale.sale_date>=data.date_from,Sale.sale_date<=data.date_to),and_(Sale.sale_date>=old_from,Sale.sale_date<=old_to))
 conditions=[scope]
 if data.departments:conditions.append(Sale.department.in_(data.departments))
 result=await db.stream_scalars(select(normalized).where(*conditions).distinct().execution_options(yield_per=512))
 clients=[]
 try:
  async for batch in result.partitions(512):
   ensure_headroom()
   clients.extend(clients_for_managers(batch,mapping,data.managers))
 finally:await result.close()
 diagnostics("manager_clients", result_rows=len(clients), container_bytes=sys.getsizeof(clients))
 return clients

def _dataset_cache_key(data:AnalyticsRequest,revision:int) -> str:
 payload=data.model_dump(mode="json")
 payload["group_by"]="product"
 for field in ("departments","managers","brands","manufacturers","subcategories"):
  payload[field]=sorted(payload[field],key=str.casefold)
 return f"{revision}:"+json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))

async def _build_dataset(data,db):
 if data.group_by not in GROUP_FIELDS:raise HTTPException(422,"Неизвестная группировка")
 started=time.perf_counter();old_from,old_to=_dates(data);manager_clients=await _manager_clients(data,db,old_from,old_to)
 stage=time.perf_counter();current=await _period_rows(data,db,data.date_from,data.date_to,manager_clients);current_ms=(time.perf_counter()-stage)*1000
 diagnostics("current_period", elapsed_ms=current_ms, result_rows=len(current), container_bytes=sys.getsizeof(current)+sum(sys.getsizeof(row) for row in current))
 stage=time.perf_counter();old=await _period_rows(data,db,old_from,old_to,manager_clients);comparison_ms=(time.perf_counter()-stage)*1000
 diagnostics("comparison_period", elapsed_ms=comparison_ms, result_rows=len(old), container_bytes=sys.getsizeof(old)+sum(sys.getsizeof(row) for row in old))
 stage=time.perf_counter();catalog=await local_catalog(chain(current,old),db) if settings.product_analytics_local_catalog_enabled else await _catalog(chain(current,old));catalog_ms=(time.perf_counter()-stage)*1000
 current_count=len(current);old_count=len(old)
 diagnostics("catalog", current_rows=current_count, comparison_rows=old_count, catalog_keys=len(catalog), container_bytes=sys.getsizeof(catalog), elapsed_ms=catalog_ms)
 ensure_headroom()
 stage=time.perf_counter();rows=await run_cpu(merge_periods,current,old,catalog)
 if settings.product_analytics_local_catalog_enabled:
  for row in rows:
   info=catalog.get(row["key"],{})
   row.update(catalog_status=info.get("catalog_status","not_synced"),catalog_synced_at=info.get("catalog_synced_at"),horeca=info.get("horeca"))
  diagnostics("local_catalog_coverage", **coverage(rows)["counts"])
 del current,old,catalog
 def process():
  selected=filter_values(rows,"brand",data.brands)
  selected=filter_values(selected,"manufacturer",data.manufacturers)
  selected=filter_subcategories(selected,data.subcategories)
  return group_rows(selected,data.group_by)
 rows=await run_cpu(process);processing_ms=(time.perf_counter()-stage)*1000
 logger.info("product_analytics stages_ms current=%.1f comparison=%.1f catalog=%.1f processing=%.1f total=%.1f current_rows=%s comparison_rows=%s result_rows=%s",
  current_ms,comparison_ms,catalog_ms,processing_ms,(time.perf_counter()-started)*1000,current_count,old_count,len(rows))
 diagnostics("build", elapsed_ms=(time.perf_counter()-started)*1000, result_rows=len(rows))
 return rows,old_from,old_to,manager_clients

async def _dataset(data,db):
 if data.group_by not in GROUP_FIELDS:raise HTTPException(422,"Неизвестная группировка")
 if settings.product_analytics_local_catalog_enabled:await begin_catalog_snapshot(db)
 # max(id) использует PK-индекс и инвалидирует кеш между worker-процессами.
 revision=int(await db.scalar(select(func.max(Sale.id))) or 0)
 source_revision=f"local-catalog:{await catalog_revision(db)}:" if settings.product_analytics_local_catalog_enabled else "legacy-catalog:"
 key=source_revision+_dataset_cache_key(data,revision)
 product_data=data.model_copy(update={"group_by":"product"})
 result,hit=await cached_product_dataset(key,lambda:_build_dataset(product_data,db))
 logger.info("product_analytics dataset cache_hit=%s result_rows=%s",hit,len(result[0]))
 started=time.perf_counter()
 rows,old_from,old_to,manager_clients=result
 grouped=await run_cpu(group_rows,rows,data.group_by)
 postprocess_ms=(time.perf_counter()-started)*1000
 diagnostics("postprocess", elapsed_ms=postprocess_ms, input_rows=len(rows), result_rows=len(grouped))
 logger.info("product_analytics dataset postprocess_ms=%.3f cache_hit=%s input_rows=%s result_rows=%s",
  postprocess_ms,hit,len(rows),len(grouped),extra={"postprocess_ms":postprocess_ms,"cache_hit":hit,"input_rows":len(rows),"result_rows":len(grouped)})
 return grouped,old_from,old_to,manager_clients

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

def _options_pagination(payload):
 if not isinstance(payload,dict):return {}
 result={}
 for key in ("data","results"):
  if isinstance(payload.get(key),dict):result.update(_options_pagination(payload[key]))
 result.update({key:payload[key] for key in ("total","pages","total_pages","has_next") if key in payload})
 if isinstance(payload.get("pagination"),dict):result.update(payload["pagination"])
 return result

async def _all_catalog_option_values(key:str,search:str):
 """Загружает справочник через существующий кеш отдельных страниц."""
 values={};loaded=0;page_size=100
 for page in range(1,10001):
  payload=await get_product_filter_options(key,search=search,page=page,page_size=page_size)
  items=_source(payload)
  if not items:return list(values)
  page_values=_option_values(payload)
  if not any(value not in values for value in page_values):
   raise VrCatalogError("vrcatalog повторил страницу справочника фильтра")
  values.update(dict.fromkeys(page_values));loaded+=len(items)
  pagination=_options_pagination(payload)
  pages=pagination.get("pages") or pagination.get("total_pages")
  total=pagination.get("total")
  try:
   if pagination.get("has_next") is False or (pages is not None and page>=int(pages)) or (total is not None and loaded>=int(total)):
    return list(values)
  except (TypeError,ValueError) as exc:
   raise VrCatalogError("vrcatalog вернул некорректную пагинацию фильтра") from exc
  # Явный has_next имеет приоритет над длиной страницы.
  if pagination.get("has_next") is not True and pages is None and total is None and len(items)<page_size:
   return list(values)
 raise VrCatalogError("vrcatalog превысил число страниц справочника фильтра")

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
   values=await _all_catalog_option_values(key,search)
   if values:return values
  except VrCatalogError as exc:last=exc
 if last:raise HTTPException(502,str(last))
 return []

@router.get("/catalog-options/{field}")
async def catalog_options(field:str,search:str="",db:AsyncSession=Depends(get_db)):
 if field not in {"brand","manufacturer","subcategory"}:raise HTTPException(404,"Неизвестный фильтр")
 return await local_options(db,field,search) if settings.product_analytics_local_catalog_enabled else await _catalog_options(field,search)

@router.get("/catalog-status")
async def catalog_status(db:AsyncSession=Depends(get_db)):
 if not settings.product_analytics_local_catalog_enabled:return {"enabled":False,"automatic_sync_enabled":False}
 return {"enabled":True,**await sync_status(db)}

@router.get("/name-suggestions")
async def name_suggestions(search:str=Query(min_length=4,max_length=200),db:AsyncSession=Depends(get_db)):
 value=search.strip()
 if len(value)<4:return []
 q=select(SaleItem.name).where(SaleItem.name.ilike(f"%{value}%")).distinct().order_by(SaleItem.name).limit(20)
 return list((await db.scalars(q)).all())

@router.post("/summary")
async def report_summary(data:AnalyticsRequest,db:AsyncSession=Depends(get_db)):
 # KPI всегда считаются по SKU, независимо от выбранной группировки таблицы.
 product_data=data.model_copy(update={"group_by":"product"});rows,old_from,old_to,manager_clients=await _dataset(product_data,db);result=await run_cpu(summary,rows)
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
 grouped=product_rows if data.group_by=="product" else await run_cpu(group_rows,product_rows,data.group_by)
 result=await run_cpu(summary,product_rows)
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
 items=await run_cpu(_sorted,grouped,section,sort_by,limit)
 total=len(await run_cpu(_sorted,grouped,section,sort_by,1000000))
 return {"period":{"start":data.date_from,"end":data.date_to},"comparison":{"start":old_from,"end":old_to},
         "summary":result,"items":items,"total":total,"catalog":coverage(product_rows) if settings.product_analytics_local_catalog_enabled else None}

async def _section(data,db,name,sort_by,limit):
 rows,_,_,_=await _dataset(data,db)
 def calculate():return {"items":_sorted(rows,name,sort_by,limit),"total":len(_sorted(rows,name,sort_by,1000000))}
 return await run_cpu(calculate)
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
 if settings.product_analytics_local_catalog_enabled and product.get("image_url"):
  try:await remember_detail_image(db,current,product["image_url"])
  except SQLAlchemyError:
   await db.rollback()
   logger.warning("product_analytics photo cache unavailable")
 return {"product":product,"group_by":group_by,"points":points}

def _sheet(book,title,headers,rows):
 sheet=book.create_sheet(title);sheet.append(headers)
 for row in rows:sheet.append(row)
 sheet.freeze_panes="A2"
 for i,column in enumerate(sheet.columns,1):sheet.column_dimensions[get_column_letter(i)].width=min(45,max(12,max(len(str(x.value or "")) for x in column)+2))

@router.post("/export")
async def export(data:AnalyticsRequest,db:AsyncSession=Depends(get_db)):
 rows,old_from,old_to,_=await _dataset(data,db)
 return await run_cpu(_export_workbook,rows,data.date_from,data.date_to,old_from,old_to)

def _export_workbook(rows,date_from,date_to,old_from,old_to):
 sections={"ТОП":_sorted(rows,"top","revenue",1000000),"Рост":_sorted(rows,"growth","absolute",1000000),"Падение":_sorted(rows,"decline","absolute",1000000),"Перестали продаваться":_sorted(rows,"stopped","revenue",1000000),"Новые товары":_sorted(rows,"new","revenue",1000000)};book=Workbook();book.remove(book.active);s=summary(rows);_sheet(book,"Сводка",["Показатель","Значение"],[[k,str(v)] for k,v in s.items()]+[["Основной период",f"{date_from} — {date_to}"],["Период сравнения",f"{old_from} — {old_to}"]]);headers=["Артикул","Код","Название","Бренд","Категория","Выручка","Прошлая выручка","Изменение","Изменение, %","Количество","Прошлое количество","Чеков"]
 for title,items in sections.items():_sheet(book,title,headers,[[x.get("article"),x.get("code"),x.get("name"),x.get("brand"),x.get("category"),x.get("revenue"),x.get("previous_revenue"),x.get("revenue_difference"),x.get("revenue_change"),x.get("units"),x.get("previous_units"),x.get("checks")] for x in items]);output=BytesIO();book.save(output);return Response(output.getvalue(),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":"attachment; filename=product-analytics.xlsx"})
