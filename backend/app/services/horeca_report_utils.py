from app.services.product_report_utils import product_key
from app.services.vrcatalog import is_horeca

def build_horeca_products(current_rows,three_month_rows,horeca_keys:set[str]):
 three={product_key(x[0],x[1],x[2]):float(x[3] or 0) for x in three_month_rows};result=[]
 for article,code,name,units in current_rows:
  if is_horeca(horeca_keys,article,code):continue
  key=product_key(article,code,name);result.append({"key":key,"photo":None,"article":article,"code":code,"name":name,"period_units":float(units or 0),"three_month_units":three.get(key,0)})
 return sorted(result,key=lambda item:-item["three_month_units"])
