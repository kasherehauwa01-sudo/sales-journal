from datetime import date
from decimal import Decimal
from sqlalchemy import Select, exists, or_, select
from app.models import Sale, SaleItem
def filtered_sales(q:Select, *,search=None,date_from:date|None=None,date_to:date|None=None,department=None,client=None,author=None,price_type=None,promotion=None,social=None,has_card=None,min_amount:Decimal|None=None,max_amount:Decimal|None=None,min_discount:Decimal|None=None,max_discount:Decimal|None=None):
 conditions=[]
 if search:
  s=f"%{search}%";conditions.append(or_(Sale.document_number.ilike(s),Sale.client.ilike(s),Sale.phone.ilike(s),Sale.discount_card_number.ilike(s),exists(select(SaleItem.id).where(SaleItem.sale_id==Sale.id,or_(SaleItem.name.ilike(s),SaleItem.article.ilike(s),SaleItem.code.ilike(s))))))
 for col,val in ((Sale.department,department),(Sale.client,client),(Sale.author,author),(Sale.price_type,price_type),(Sale.promotion,promotion)):
  if val:conditions.append(col==val)
 if date_from:conditions.append(Sale.sale_date>=date_from)
 if date_to:conditions.append(Sale.sale_date<=date_to)
 if social is not None:conditions.append(Sale.social==social)
 if has_card is not None:conditions.append(Sale.discount_card_number.is_not(None) if has_card else Sale.discount_card_number.is_(None))
 for col,val,op in ((Sale.total_amount,min_amount,"ge"),(Sale.total_amount,max_amount,"le"),(Sale.discount_percent,min_discount,"ge"),(Sale.discount_percent,max_discount,"le")):
  if val is not None:conditions.append(getattr(col,f"__{op}__")(val))
 return q.where(*conditions)
