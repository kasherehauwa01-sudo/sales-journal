from datetime import date
from decimal import Decimal
from sqlalchemy import Select, exists, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models import Sale, SaleItem
def filtered_sales(q:Select, *,search=None,date_from:date|None=None,date_to:date|None=None,departments=None,clients=None,client=None,author=None,price_type=None,promotion=None,social=None,discount_card_percent:Decimal|None=None,min_amount:Decimal|None=None,max_amount:Decimal|None=None,min_discount:Decimal|None=None,max_discount:Decimal|None=None):
 conditions=[]
 if search:
  s=f"%{search}%";conditions.append(or_(Sale.document_number.ilike(s),Sale.client.ilike(s),Sale.phone.ilike(s),Sale.discount_card_number.ilike(s),exists(select(SaleItem.id).where(SaleItem.sale_id==Sale.id,or_(SaleItem.name.ilike(s),SaleItem.article.ilike(s),SaleItem.code.ilike(s))))))
 for col,val in ((Sale.client,client),(Sale.author,author),(Sale.price_type,price_type),(Sale.promotion,promotion)):
  if val:conditions.append(col==val)
 if departments:conditions.append(Sale.department.in_(departments))
 if clients is not None:
  normalized=[value.strip().lower() for value in clients if value.strip()]
  conditions.append(func.lower(func.trim(Sale.client)).in_(normalized) if normalized else false())
 if date_from:conditions.append(Sale.sale_date>=date_from)
 if date_to:conditions.append(Sale.sale_date<=date_to)
 if social is not None:conditions.append(Sale.social==social)
 if discount_card_percent is not None:conditions.append(func.abs(Sale.discount_card_percent)==abs(discount_card_percent))
 for col,val,op in ((Sale.total_amount,min_amount,"ge"),(Sale.total_amount,max_amount,"le"),(Sale.discount_percent,min_discount,"ge"),(Sale.discount_percent,max_discount,"le")):
  if val is not None:conditions.append(getattr(col,f"__{op}__")(val))
 return q.where(*conditions)


async def get_sale_with_items(db: AsyncSession, sale_id: int) -> Sale | None:
 """Загружает карточку продажи единым переиспользуемым способом."""
 return await db.scalar(select(Sale).options(selectinload(Sale.items)).where(Sale.id == sale_id))


async def find_client_sales(db: AsyncSession, *, phones: set[str], names: set[str], date_from: date, date_to: date) -> list[Sale]:
 """Получает продажи всех клиентов одним SQL-запросом без загрузки товаров."""
 identities=[]
 if phones:identities.append(Sale.phone.in_(phones))
 if names:identities.append(func.lower(func.trim(Sale.client)).in_(names))
 if not identities:return []
 query=select(Sale).where(Sale.sale_date>=date_from,Sale.sale_date<=date_to,or_(*identities)).order_by(Sale.sale_date,Sale.id)
 return list((await db.scalars(query)).all())
