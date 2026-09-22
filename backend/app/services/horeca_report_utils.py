from app.services.product_report_utils import product_key
from app.services.vrcatalog import is_horeca

HORECA_PRODUCT_LIMIT=300

def build_horeca_products(current_rows,three_month_rows,horeca_keys:set[str]):
 three={product_key(x[0],x[1],x[2]):float(x[3] or 0) for x in three_month_rows};result=[]
 for article,code,name,units in current_rows:
  if is_horeca(horeca_keys,article,code):continue
  key=product_key(article,code,name);result.append({"key":key,"photo":None,"article":article,"code":code,"name":name,"period_units":float(units or 0),"three_month_units":three.get(key,0)})
 return sorted(result,key=lambda item:-item["three_month_units"])

def build_horeca_products_from_info(current_rows,three_month_rows,catalog_info:dict[str,dict]):
 three={product_key(x[0],x[1],x[2]):float(x[3] or 0) for x in three_month_rows};result=[]
 for article,code,name,units in current_rows:
  code_key=f"code:{str(code).strip().lower()}" if code and str(code).strip() else None;article_key=f"article:{str(article).strip().lower()}" if article and str(article).strip() else None
  info=catalog_info.get(code_key) if code_key else None
  if info is None and article_key:info=catalog_info.get(article_key)
  if info and info.get("horeca") is True:continue
  key=product_key(article,code,name);result.append({"key":key,"photo":info.get("image_url") if info else None,"article":article,"code":code,"name":name,"period_units":float(units or 0),"three_month_units":three.get(key,0)})
 return sorted(result,key=lambda item:-item["three_month_units"])

def omir_codes(products:list[dict])->list[str]:
 return [item.get("code") or "" for item in products]

def limit_horeca_products(products:list[dict])->list[dict]:
 return products[:HORECA_PRODUCT_LIMIT]
