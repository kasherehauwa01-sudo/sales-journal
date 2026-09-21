import asyncio,json,time
from urllib.error import HTTPError
from urllib.parse import quote,urlencode
from urllib.request import Request,urlopen

cache:tuple[float,set[str]]|None=None
class VrCatalogError(RuntimeError):pass

def _source(payload):
 if isinstance(payload,list):return payload
 if isinstance(payload,dict):
  for key in ("products","items","data","results"):
   value=payload.get(key)
   if isinstance(value,list):return value
   if isinstance(value,dict):
    nested=_source(value)
    if nested:return nested
 return []

def _property_is_horeca(item):
 properties=item.get("properties") or item.get("attributes") or item.get("Свойства")
 if properties is None:return True  # endpoint уже отфильтрован по свойству
 if isinstance(properties,dict):return str(properties.get("HoReCa","")).strip().lower()=="horeca"
 if isinstance(properties,list):
  return any(str(value.get("name") or value.get("property") or value.get("Название") or "").strip().lower()=="horeca" and str(value.get("value") or value.get("Значение") or "").strip().lower()=="horeca" for value in properties if isinstance(value,dict))
 return False

def horeca_keys(payload):
 result=set()
 for item in _source(payload):
  if not isinstance(item,dict) or not _property_is_horeca(item):continue
  for prefix,names in (("article",("article","sku","article_number","Артикул")),("code",("code","product_code","Код"))):
   value=next((item.get(name) for name in names if item.get(name)),None)
   if value:result.add(f"{prefix}:{str(value).strip().lower()}")
 return result

def _request():
 from app.config import settings
 query=urlencode({"property":"HoReCa","property_value":"HoReCa","limit":10000});headers={"Accept":"application/json"}
 if settings.vrcatalog_api_token:headers["Authorization"]=f"Bearer {settings.vrcatalog_api_token}"
 last=None
 for path in (f"/products?{query}",f"/catalog/products?{query}"):
  try:
   with urlopen(Request(f"{settings.vrcatalog_api_url.rstrip('/')}{path}",headers=headers),timeout=30) as response:return horeca_keys(json.load(response))
  except HTTPError as exc:
   last=exc
   if exc.code not in {404,405,422}:raise
 raise last or RuntimeError("Не найден endpoint товаров vrcatalog")

async def get_horeca_keys():
 global cache
 if cache and time.monotonic()-cache[0]<300:return cache[1]
 try:values=await asyncio.to_thread(_request)
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc
 cache=(time.monotonic(),values);return values

def is_horeca(keys:set[str],article,code):
 return any(value and f"{prefix}:{str(value).strip().lower()}" in keys for prefix,value in (("article",article),("code",code)))

def _integration_request(path: str, method: str = "GET", payload=None):
 from app.config import settings
 headers={"Accept":"application/json"}
 if settings.vrcatalog_api_token:
  headers["Authorization"]=f"Bearer {settings.vrcatalog_api_token}"
 data=None
 if payload is not None:
  data=json.dumps(payload).encode("utf-8")
  headers["Content-Type"]="application/json"
 request=Request(
  f"{settings.vrcatalog_api_url.rstrip('/')}{path}",
  data=data,
  headers=headers,
  method=method,
 )
 with urlopen(request,timeout=30) as response:
  return json.load(response)

async def get_product_filters():
 try:
  return await asyncio.to_thread(
   _integration_request,
   "/integration/product-filters",
  )
 except Exception as exc:
  raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc

async def get_product_filter_options(filter_key: str, search: str = "", page: int = 1, page_size: int = 100):
 try:
  query=urlencode({
   "search":search,
   "page":page,
   "page_size":page_size,
  })
  return await asyncio.to_thread(
   _integration_request,
   f"/integration/product-filters/{quote(filter_key, safe='')}/options?{query}",
  )
 except Exception as exc:
  raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc

async def search_catalog_products(
 filters: dict,
 search: str = "",
 page: int = 1,
 page_size: int = 100,
):
 payload={
  "filters":filters,
  "search":search,
  "page":page,
  "page_size":page_size,
 }
 try:
  return await asyncio.to_thread(
   _integration_request,
   "/integration/products/search",
   "POST",
   payload,
  )
 except Exception as exc:
  raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc
