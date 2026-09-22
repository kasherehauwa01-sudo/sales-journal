import asyncio,json,time
from urllib.parse import urljoin,urlparse
from urllib.parse import quote,urlencode
from urllib.request import Request,urlopen

cache:tuple[float,set[str]]|None=None
image_cache:tuple[float,dict[str,str]]|None=None
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

def _image_url(item):
 value=next((item.get(key) for key in ("photo","image","image_url","photo_url","thumbnail","main_image","main_image_url","main_photo","main_photo_url") if item.get(key)),None)
 if not value:
  collection=next((item.get(key) for key in ("images","photos","pictures") if isinstance(item.get(key),list) and item[key]),None)
  if collection:value=collection[0]
 if isinstance(value,dict):value=next((value.get(key) for key in ("url","src","path","image_url","photo_url","file_url","download_url") if value.get(key)),None)
 return value if isinstance(value,str) else None

def _absolute_image_url(value:str,base_url:str):
 if value.startswith("data:") or urlparse(value).scheme:return value
 return urljoin(f"{base_url.rstrip('/')}/",value)

def catalog_product_images(payload,base_url:str=""):
 result={}
 for item in _source(payload):
  if not isinstance(item,dict):continue
  image=_image_url(item)
  if not image:continue
  if base_url:image=_absolute_image_url(image,base_url)
  for prefix,names in (("article",("article","sku","article_number","Артикул")),("code",("code","product_code","Код"))):
   value=next((item.get(name) for name in names if item.get(name)),None)
   if value:result[f"{prefix}:{str(value).strip().lower()}"]=image
 return result

def _integration_search(payload):
 from app.config import settings
 headers={"Accept":"application/json","Content-Type":"application/json"}
 if settings.vrcatalog_api_token:headers["Authorization"]=f"Bearer {settings.vrcatalog_api_token}"
 request=Request(f"{settings.vrcatalog_api_url.rstrip('/')}/integration/products/search",data=json.dumps(payload).encode(),headers=headers,method="POST")
 with urlopen(request,timeout=30) as response:return json.load(response)

def _integration_get(path:str,params:dict|None=None):
 from app.config import settings
 headers={"Accept":"application/json"}
 if settings.vrcatalog_api_token:headers["Authorization"]=f"Bearer {settings.vrcatalog_api_token}"
 query=f"?{urlencode({key:value for key,value in (params or {}).items() if value is not None})}" if params else ""
 request=Request(f"{settings.vrcatalog_api_url.rstrip('/')}/{path.lstrip('/')}{query}",headers=headers,method="GET")
 with urlopen(request,timeout=30) as response:return json.load(response)

async def get_product_filters():
 try:return await asyncio.to_thread(_integration_get,"integration/product-filters")
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc

async def get_product_filter_options(filter_key:str,*,search:str="",page:int=1,page_size:int=100):
 try:return await asyncio.to_thread(_integration_get,f"integration/product-filters/{quote(filter_key,safe='')}/options",{"search":search,"page":page,"page_size":page_size})
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc

async def search_catalog_products(*,filters:dict,page:int=1,page_size:int=500,search:str=""):
 payload={"filters":filters,"page":page,"page_size":page_size}
 if search:payload["search"]=search
 try:return await asyncio.to_thread(_integration_search,payload)
 except VrCatalogError:raise
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc

def _pagination(payload):
 if not isinstance(payload,dict):return {}
 pagination=payload.get("pagination") if isinstance(payload.get("pagination"),dict) else payload
 result={key:pagination.get(key) for key in ("total","page","page_size","pages","total_pages","has_next")}
 if result["total"] is None:
  nested=next((payload.get(key) for key in ("data","result") if isinstance(payload.get(key),dict)),None)
  if nested:return _pagination(nested)
 return result

async def get_horeca_keys():
 global cache
 if cache and time.monotonic()-cache[0]<300:return cache[1]
 try:
  values=set();page=1;page_size=500;loaded=0
  while page<=10000:
   payload=await search_catalog_products(filters={"property:HoReCa":["HoReCa"]},page=page,page_size=page_size);items=_source(payload)
   if not items:break
   values.update(horeca_keys(items));loaded+=len(items);pagination=_pagination(payload);total=pagination.get("total");pages=pagination.get("pages") or pagination.get("total_pages");current=pagination.get("page") or page
   if total is not None and loaded>=int(total):break
   if pages is not None and int(current)>=int(pages):break
   if pagination.get("has_next") is False:break
   if len(items)<page_size:break
   page+=1
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc
 cache=(time.monotonic(),values);return values

async def get_catalog_images(wanted_keys:set[str]):
 global image_cache

 values=dict(image_cache[1]) if image_cache and time.monotonic()-image_cache[0]<300 else {}

 missing=wanted_keys-set(values)

 if not missing:
  return {key:values[key] for key in wanted_keys if key in values}

 try:
  for wanted_key in missing:
   if ":" not in wanted_key:
    continue

   _,search_value=wanted_key.split(":",1)
   search_value=search_value.strip()

   if not search_value:
    continue

   payload=await search_catalog_products(
    filters={},
    search=search_value,
    page=1,
    page_size=50,
   )

   items=_source(payload)
   page_images=catalog_product_images(items)

   if any(
    not urlparse(value).scheme and not value.startswith("data:")
    for value in page_images.values()
   ):
    from app.config import settings
    page_images=catalog_product_images(items,settings.vrcatalog_api_url)

   values.update(page_images)

 except Exception as exc:
  raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc

 image_cache=(time.monotonic(),values)

 return {
  key:values[key]
  for key in wanted_keys
  if key in values
 }


def is_horeca(keys:set[str],article,code):
 return any(value and f"{prefix}:{str(value).strip().lower()}" in keys for prefix,value in (("article",article),("code",code)))
