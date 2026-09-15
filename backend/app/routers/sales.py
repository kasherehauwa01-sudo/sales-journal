from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import Sale, SaleItem
from app.repositories.sales import filtered_sales
from app.schemas import ItemOut, SaleOut, SalePage
router=APIRouter(prefix="/sales",tags=["Продажи"])
@router.get("",response_model=SalePage)
async def list_sales(page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=200),sort_by:str="sale_date",sort_dir:str="desc",search:str|None=None,date_from:date|None=None,date_to:date|None=None,departments:list[str]|None=Query(None),client:str|None=None,author:str|None=None,price_type:str|None=None,promotion:str|None=None,social:bool|None=None,discount_card_percent:Decimal|None=None,min_amount:Decimal|None=None,max_amount:Decimal|None=None,min_discount:Decimal|None=None,max_discount:Decimal|None=None,db:AsyncSession=Depends(get_db)):
 kw=locals().copy();kw.pop("db");[kw.pop(x) for x in ("page","page_size","sort_by","sort_dir")]
 base=filtered_sales(select(Sale),**kw);total=await db.scalar(select(func.count()).select_from(base.subquery())) or 0
 allowed={x.name:getattr(Sale,x.name) for x in Sale.__table__.columns};col=allowed.get(sort_by,Sale.sale_date);order=desc(col) if sort_dir=="desc" else asc(col)
 items=(await db.scalars(base.order_by(order,desc(Sale.id)).offset((page-1)*page_size).limit(page_size))).unique().all()
 return SalePage(items=items,total=total,page=page,page_size=page_size,pages=max(1,(total+page_size-1)//page_size))
@router.get("/{sale_id}",response_model=SaleOut)
async def get_sale(sale_id:int,db:AsyncSession=Depends(get_db)):
 sale=await db.get(Sale,sale_id)
 if not sale:raise HTTPException(404,"Продажа не найдена")
 return sale
@router.get("/{sale_id}/items",response_model=list[ItemOut])
async def items(sale_id:int,db:AsyncSession=Depends(get_db)):return (await db.scalars(select(SaleItem).where(SaleItem.sale_id==sale_id))).all()
