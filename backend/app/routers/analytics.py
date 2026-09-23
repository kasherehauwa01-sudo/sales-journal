from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import Sale, SaleItem
from app.services.clients_vr import ClientsVrError,get_client_buyer_types,get_client_managers
from app.services.sales_client_filters import get_sales_buyer_type_clients
from app.services.manager_analytics import aggregate_buyer_type_dynamics,aggregate_manager_sales
router=APIRouter(prefix="/analytics",tags=["Аналитика"])
def filters(q,date_from=None,date_to=None,department=None,client=None,price_type=None,promotion=None,clients=None):
 for col,val in ((Sale.department,department),(Sale.client,client),(Sale.price_type,price_type),(Sale.promotion,promotion)):
  if val:q=q.where(col==val)
 if clients is not None:
  normalized=[value.strip().lower() for value in clients];q=q.where(func.lower(func.trim(Sale.client)).in_(normalized) if normalized else False)
 if date_from:q=q.where(Sale.sale_date>=date_from)
 if date_to:q=q.where(Sale.sale_date<=date_to)
 return q
async def resolve_buyer_type(db, buyer_type):
 if not buyer_type:return None
 try:return await get_sales_buyer_type_clients(db, buyer_type)
 except ClientsVrError as exc:raise HTTPException(502,str(exc)) from exc
def pct(cur,prev):return None if not prev else float((cur-prev)/prev*100)
async def metrics(db,q):
 sub=q.subquery();item=select(func.coalesce(func.sum(SaleItem.quantity),0)).join(sub,SaleItem.sale_id==sub.c.id).scalar_subquery()
 row=(await db.execute(select(func.coalesce(func.sum(sub.c.total_amount),0),func.count(sub.c.id),func.coalesce(func.avg(sub.c.total_amount),0),func.coalesce(func.sum(sub.c.base_amount),0),func.coalesce(func.avg(sub.c.discount_percent),0),func.coalesce(func.sum(sub.c.base_amount-sub.c.total_amount),0),item,func.coalesce(func.avg(case((sub.c.discount_percent>0,1),else_=0))*100,0)))).one()
 keys=("revenue","sales_count","average_check","base_amount","average_discount","discount_amount","units","discounted_share");d={k:float(v) for k,v in zip(keys,row)};d["items_per_check"]=d["units"]/d["sales_count"] if d["sales_count"] else 0;d["average_unit_price"]=d["revenue"]/d["units"] if d["units"] else 0;return d
@router.get("/overview")
async def overview(date_from:date|None=None,date_to:date|None=None,department:str|None=None,client:str|None=None,price_type:str|None=None,promotion:str|None=None,buyer_type:str|None=None,db:AsyncSession=Depends(get_db)):
 buyer_clients=await resolve_buyer_type(db,buyer_type)
 current=await metrics(db,filters(select(Sale),date_from,date_to,department,client,price_type,promotion,clients=buyer_clients));previous={}
 if date_from and date_to:
  length=(date_to-date_from).days+1;previous=await metrics(db,filters(select(Sale),date_from-timedelta(days=length),date_from-timedelta(days=1),department,client,price_type,promotion,clients=buyer_clients))
 return {"current":current,"previous":previous,"changes":{k:pct(v,previous.get(k,0)) for k,v in current.items()} if previous else {}}
@router.get("/dynamics")
async def dynamics(group_by:str=Query("day",pattern="^(day|week|month|quarter|year)$"),date_from:date|None=None,date_to:date|None=None,department:str|None=None,client:str|None=None,price_type:str|None=None,promotion:str|None=None,buyer_type:str|None=None,db:AsyncSession=Depends(get_db)):
 buyer_clients=await resolve_buyer_type(db,buyer_type)
 bucket=func.date_trunc(group_by,Sale.sale_date).label("period")
 try:client_types=await get_client_buyer_types()
 except ClientsVrError as exc:raise HTTPException(502,str(exc)) from exc
 q=filters(select(bucket,Sale.client,func.sum(Sale.total_amount).label("revenue"),func.count(Sale.id).label("checks")).group_by(bucket,Sale.client).order_by(bucket),date_from,date_to,department,client,price_type,promotion,clients=buyer_clients)
 return aggregate_buyer_type_dynamics((await db.execute(q)).all(),client_types)
@router.get("/departments")
async def departments(date_from:date|None=None,date_to:date|None=None,department:str|None=None,buyer_type:str|None=None,db:AsyncSession=Depends(get_db)):
 buyer_clients=await resolve_buyer_type(db,buyer_type)
 item=select(SaleItem.sale_id,func.sum(SaleItem.quantity).label("units")).group_by(SaleItem.sale_id).subquery();q=filters(select(Sale.department,func.sum(Sale.total_amount).label("revenue"),func.count(Sale.id).label("sales"),func.avg(Sale.total_amount).label("average_check"),func.coalesce(func.sum(item.c.units),0).label("units"),func.avg(Sale.discount_percent).label("average_discount")).outerjoin(item,item.c.sale_id==Sale.id).group_by(Sale.department).order_by(func.sum(Sale.total_amount).desc()),date_from,date_to,department,clients=buyer_clients)
 return [{k:(float(v) if k not in {"department","sales"} and v is not None else v) for k,v in r._mapping.items()} for r in (await db.execute(q))]
@router.get("/products")
async def products(date_from:date|None=None,date_to:date|None=None,department:str|None=None,buyer_type:str|None=None,limit:int=Query(100,le=500),db:AsyncSession=Depends(get_db)):
 buyer_clients=await resolve_buyer_type(db,buyer_type)
 q=filters(select(SaleItem.article,SaleItem.code,SaleItem.name,func.sum(SaleItem.quantity*SaleItem.actual_price).label("revenue"),func.sum(SaleItem.quantity).label("units"),func.count(distinct(SaleItem.sale_id)).label("checks"),func.avg(SaleItem.actual_price).label("average_price"),func.avg(SaleItem.base_price).label("average_base_price"),func.count(distinct(Sale.department)).label("departments")).join(Sale,Sale.id==SaleItem.sale_id),date_from,date_to,department,clients=buyer_clients).group_by(SaleItem.article,SaleItem.code,SaleItem.name).order_by(func.sum(SaleItem.quantity*SaleItem.actual_price).desc()).limit(limit)
 return [{k:(float(v) if hasattr(v,"as_integer_ratio") else v) for k,v in r._mapping.items()} for r in (await db.execute(q))]
@router.get("/clients")
async def clients(date_from:date|None=None,date_to:date|None=None,department:str|None=None,buyer_type:str|None=None,limit:int=100,db:AsyncSession=Depends(get_db)):
 buyer_clients=await resolve_buyer_type(db,buyer_type)
 q=filters(select(Sale.client,func.max(Sale.phone).label("phone"),func.max(Sale.discount_card_number).label("card"),func.count().label("purchases"),func.sum(Sale.total_amount).label("revenue"),func.avg(Sale.total_amount).label("average_check"),func.min(Sale.sale_date).label("first_purchase"),func.max(Sale.sale_date).label("last_purchase"),func.avg(Sale.discount_percent).label("average_discount")).where(Sale.client.is_not(None),func.trim(Sale.client)!=""),date_from,date_to,department,clients=buyer_clients).group_by(Sale.client).order_by(func.sum(Sale.total_amount).desc()).limit(limit)
 return [{k:(float(v) if hasattr(v,"as_integer_ratio") else v) for k,v in r._mapping.items()} for r in (await db.execute(q))]
@router.get("/managers")
async def managers(date_from:date|None=None,date_to:date|None=None,department:str|None=None,buyer_type:str|None=None,db:AsyncSession=Depends(get_db)):
 buyer_clients=await resolve_buyer_type(db,buyer_type)
 try:client_managers=await get_client_managers()
 except ClientsVrError as exc:raise HTTPException(502,str(exc)) from exc
 q=filters(select(Sale.client,func.sum(Sale.total_amount).label("revenue"),func.count(Sale.id).label("sales_count")).group_by(Sale.client),date_from,date_to,department,clients=buyer_clients)
 return aggregate_manager_sales((await db.execute(q)).all(),client_managers)
@router.get("/discounts")
async def discounts(date_from:date|None=None,date_to:date|None=None,department:str|None=None,buyer_type:str|None=None,db:AsyncSession=Depends(get_db)):
 buyer_clients=await resolve_buyer_type(db,buyer_type)
 q=filters(select(func.sum(Sale.base_amount).label("base_amount"),func.sum(Sale.total_amount).label("revenue"),func.sum(Sale.base_amount-Sale.total_amount).label("discount_amount"),func.avg(Sale.discount_percent).label("average_discount"),func.sum(case((Sale.discount_percent>0,1),else_=0)).label("with_discount"),func.sum(case((Sale.discount_percent<=0,1),else_=0)).label("without_discount")),date_from,date_to,department,clients=buyer_clients);r=(await db.execute(q)).one();return {k:(float(v or 0)) for k,v in r._mapping.items()}
@router.get("/promotions")
async def promotions(date_from:date|None=None,date_to:date|None=None,department:str|None=None,buyer_type:str|None=None,db:AsyncSession=Depends(get_db)):
 buyer_clients=await resolve_buyer_type(db,buyer_type)
 q=filters(select(Sale.promotion,func.count().label("sales"),func.sum(Sale.total_amount).label("revenue"),func.avg(Sale.total_amount).label("average_check"),func.avg(Sale.discount_percent).label("average_discount"),func.count(distinct(Sale.client)).label("clients"),func.count(distinct(Sale.department)).label("departments")).group_by(Sale.promotion).order_by(func.sum(Sale.total_amount).desc()),date_from,date_to,department,clients=buyer_clients);return [{k:(float(v) if hasattr(v,"as_integer_ratio") else v) for k,v in r._mapping.items()} for r in (await db.execute(q))]
@router.get("/checks")
async def checks(date_from:date|None=None,date_to:date|None=None,department:str|None=None,buyer_type:str|None=None,db:AsyncSession=Depends(get_db)):
 buyer_clients=await resolve_buyer_type(db,buyer_type)
 q=filters(select(Sale.id,Sale.total_amount),date_from,date_to,department,clients=buyer_clients).subquery();units=select(SaleItem.sale_id,func.sum(SaleItem.quantity).label("units")).group_by(SaleItem.sale_id).subquery();joined=select(q.c.total_amount,func.coalesce(units.c.units,0).label("units")).outerjoin(units,units.c.sale_id==q.c.id).subquery();r=(await db.execute(select(func.avg(joined.c.total_amount),func.percentile_cont(.5).within_group(joined.c.total_amount),func.avg(joined.c.units),func.percentile_cont(.5).within_group(joined.c.units),func.sum(joined.c.total_amount)/func.nullif(func.sum(joined.c.units),0)))).one();amounts=(await db.execute(select(case((joined.c.total_amount<500,"до 500"),(joined.c.total_amount<1000,"500–1 000"),(joined.c.total_amount<2000,"1 000–2 000"),(joined.c.total_amount<5000,"2 000–5 000"),(joined.c.total_amount<10000,"5 000–10 000"),else_="более 10 000").label("bucket"),func.count()).group_by("bucket"))).all();return {"average_check":float(r[0] or 0),"median_check":float(r[1] or 0),"average_items":float(r[2] or 0),"median_items":float(r[3] or 0),"average_unit_price":float(r[4] or 0),"amount_distribution":[{"bucket":x[0],"count":x[1]} for x in amounts]}
