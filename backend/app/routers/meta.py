from fastapi import APIRouter,Depends,Query
from sqlalchemy import distinct,func,select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import Sale
router=APIRouter(tags=["Справочники"])
@router.get("/filters")
async def values(db:AsyncSession=Depends(get_db)):
 async def vals(col):return [x for x in (await db.scalars(select(col).where(col.is_not(None),func.trim(col)!="").distinct().order_by(col).limit(1000))).all()]
 card_values=(await db.scalars(select(Sale.discount_card_percent).where(Sale.discount_card_percent.is_not(None),Sale.discount_card_percent!=0).distinct())).all()
 return {"departments":await vals(Sale.department),"clients":await vals(Sale.client),"price_types":await vals(Sale.price_type),"discount_card_percents":sorted({float(abs(x)) for x in card_values})}
@router.get("/suggestions")
async def suggestions(q:str=Query(min_length=1,max_length=200),field:str=Query("search",pattern="^(search|client)$"),db:AsyncSession=Depends(get_db)):
 value=q.strip()
 if not value:return []
 term=f"%{value}%"
 async def matches(col,label):
  values=(await db.scalars(select(distinct(col)).where(col.is_not(None),func.trim(col)!="",col.ilike(term)).order_by(col).limit(10))).all()
  return [{"value":item,"label":f"{label}: {item}" if label else item} for item in values]
 clients=await matches(Sale.client,"" if field=="client" else "Клиент")
 if field=="client":return clients
 groups=[clients,await matches(Sale.phone,"Телефон"),await matches(Sale.document_number,"Документ")]
 return [item for index in range(10) for group in groups for item in group[index:index+1]][:10]
