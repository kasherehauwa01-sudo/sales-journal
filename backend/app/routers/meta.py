from fastapi import APIRouter,Depends
from sqlalchemy import distinct,func,select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import Sale
router=APIRouter(tags=["Справочники"])
@router.get("/filters")
async def values(db:AsyncSession=Depends(get_db)):
 async def vals(col):return [x for x in (await db.scalars(select(distinct(col)).where(col.is_not(None)).order_by(col).limit(1000))).all() if x]
 card_percents=(await db.scalars(select(distinct(func.abs(Sale.discount_card_percent))).where(Sale.discount_card_percent.is_not(None),Sale.discount_card_percent!=0).order_by(func.abs(Sale.discount_card_percent)))).all()
 return {"departments":await vals(Sale.department),"authors":await vals(Sale.author),"price_types":await vals(Sale.price_type),"promotions":await vals(Sale.promotion),"discount_card_percents":[float(x) for x in card_percents]}
