from fastapi import APIRouter,Depends
from sqlalchemy import distinct,select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import Sale
router=APIRouter(tags=["Справочники"])
@router.get("/filters")
async def values(db:AsyncSession=Depends(get_db)):
 async def vals(col):return [x for x in (await db.scalars(select(distinct(col)).where(col.is_not(None)).order_by(col).limit(1000))).all() if x]
 return {"departments":await vals(Sale.department),"authors":await vals(Sale.author),"price_types":await vals(Sale.price_type),"promotions":await vals(Sale.promotion)}
