import asyncio,json,logging,time,sys,threading
from app.services.product_analytics_runtime import run_cpu, diagnostics, heavy_operation, ensure_headroom
from app.services.product_analytics_cache import _bounded_size
from collections import OrderedDict
from urllib.parse import urljoin,urlparse
from urllib.parse import quote,urlencode
from urllib.request import Request,urlopen

cache:tuple[float,set[str]]|None=None
image_cache:tuple[float,dict[str,str]]|None=None

# Кеш информации о товарах CatalogVR.
# Нужен для аналитических отчетов, чтобы повторно не запрашивать
# десятки тысяч уже известных товаров через batch-info.
catalog_info_cache: dict[str, tuple[float, dict | None]] = {}
CATALOG_INFO_CACHE_TTL = 1800
CATALOG_INFO_CACHE_MAX_ENTRIES = 30000
# Conservative accounting charges code/article aliases separately.
CATALOG_INFO_CACHE_MAX_BYTES = 24 * 1024 * 1024
_catalog_info_sizes = {}
_catalog_info_bytes = 0
CATALOG_BATCH_SIZE = 250
CATALOG_BATCH_CONCURRENCY = 2
logger=logging.getLogger(__name__)

# Небольшой stale-if-error кеш защищает справочники фильтров от кратковременных
# таймаутов CatalogVR. В ключах нет токена, а число поисковых запросов ограничено.
DIRECTORY_CACHE_TTL = 300
DIRECTORY_CACHE_STALE_TTL = 86400
DIRECTORY_CACHE_MAX_ENTRIES = 128
directory_cache: OrderedDict[str, tuple[float, object]] = OrderedDict()
directory_locks: dict[str, asyncio.Lock] = {}

# Карта категорий хранится отдельно от тяжёлой информации о товаре. Отдельное
# множество позволяет отличить отрицательный результат от category=null.
catalog_category_cache: dict[str, tuple[float, dict]] = {}
catalog_category_negative_cache: dict[str, float] = {}
CATALOG_CATEGORY_CACHE_TTL = 1800
CATALOG_CATEGORY_MAP_LIMIT = 5000

catalog_tree_cache: tuple[float,list[dict]]|None = None
catalog_filter_keys_cache: dict[tuple[tuple[str,tuple[str,...]],...],tuple[float,set[str]]] = {}
CATALOG_TREE_CACHE_TTL = 300
CATALOG_FILTER_KEYS_CACHE_TTL = 300

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
 value=next((item.get(key) for key in ("url","src","path","relative_url","photo","image","picture","image_url","imageUrl","photo_url","photoUrl","picture_url","photo_path","image_path","thumbnail","thumbnail_url","preview","preview_url","main_image","main_image_url","mainImageUrl","main_photo","main_photo_url","mainPhotoUrl") if item.get(key)),None)
 if not value:
  collection=next((item.get(key) for key in ("images","photos","pictures") if isinstance(item.get(key),list) and item[key]),None)
  if collection:value=collection[0]
 if not value:
  # Некоторые версии batch-info оборачивают главное фото в media/cover.
  container=next((item.get(key) for key in ("media","main_media","cover","primary_image","primary_photo") if isinstance(item.get(key),dict)),None)
  if container:value=_image_url(container)
 if isinstance(value,dict):value=next((value.get(key) for key in ("url","src","path","relative_url","image_url","imageUrl","photo_url","photoUrl","photo_path","image_path","file_url","download_url") if value.get(key)),None)
 return value if isinstance(value,str) else None

def _absolute_image_url(value:str,base_url:str):
 if value.startswith("data:") or urlparse(value).scheme:return value
 parsed=urlparse(base_url)
 catalog_path=parsed.path.rstrip("/")
 if catalog_path.endswith("/api"):catalog_path=catalog_path[:-4]
 if value.startswith("/"):
  # Root-relative ссылки CatalogVR должны оставаться внутри /vr/catalog,
  # а не уходить в корень kvasmix.ru или разрешаться относительно /vr/sales/.
  if value.startswith("/vr/"):return f"{parsed.scheme}://{parsed.netloc}{value}"
  return f"{parsed.scheme}://{parsed.netloc}{catalog_path}{value}"
 # Относительные media-ссылки также обслуживаются публичным корнем CatalogVR,
 # а не integration API и тем более не BASE_PATH Sales Journal.
 return urljoin(f"{parsed.scheme}://{parsed.netloc}{catalog_path.rstrip('/')}/",value)

def catalog_item_image_url(item:dict,base_url:str=""):
 """Извлекает фото из поддерживаемых полей CatalogVR и нормализует URL."""
 value=_image_url(item)
 if not value:return None
 return _absolute_image_url(value,base_url) if base_url else value

def catalog_product_images(payload,base_url:str=""):
 result={}
 for item in _source(payload):
  if not isinstance(item,dict):continue
  image=catalog_item_image_url(item)
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

def _integration_batch(payload):
 from app.config import settings
 headers={"Accept":"application/json","Content-Type":"application/json"}
 if settings.vrcatalog_api_token:headers["Authorization"]=f"Bearer {settings.vrcatalog_api_token}"
 request=Request(f"{settings.vrcatalog_api_url.rstrip('/')}/integration/products/batch-info",data=json.dumps(payload).encode(),headers=headers,method="POST")
 with urlopen(request,timeout=30) as response:return json.load(response)

def _integration_category_map(payload):
 from app.config import settings
 headers={"Accept":"application/json","Content-Type":"application/json"}
 if settings.vrcatalog_api_token:headers["Authorization"]=f"Bearer {settings.vrcatalog_api_token}"
 request=Request(f"{settings.vrcatalog_api_url.rstrip('/')}/integration/products/category-map",data=json.dumps(payload).encode(),headers=headers,method="POST")
 with urlopen(request,timeout=30) as response:return json.load(response)

def _integration_get(path:str,params:dict|None=None):
 from app.config import settings
 headers={"Accept":"application/json"}
 if settings.vrcatalog_api_token:headers["Authorization"]=f"Bearer {settings.vrcatalog_api_token}"
 query=f"?{urlencode({key:value for key,value in (params or {}).items() if value is not None})}" if params else ""
 request=Request(f"{settings.vrcatalog_api_url.rstrip('/')}/{path.lstrip('/')}{query}",headers=headers,method="GET")
 with urlopen(request,timeout=30) as response:return json.load(response)

async def _cached_integration_get(path:str,params:dict|None=None):
 key=json.dumps([path,sorted((params or {}).items())],ensure_ascii=False,separators=(",",":"))
 now=time.monotonic();entry=directory_cache.get(key)
 if entry and now-entry[0]<DIRECTORY_CACHE_TTL:
  directory_cache.move_to_end(key);return entry[1]
 lock=directory_locks.setdefault(key,asyncio.Lock())
 try:
  async with lock:
   now=time.monotonic();entry=directory_cache.get(key)
   if entry and now-entry[0]<DIRECTORY_CACHE_TTL:
    directory_cache.move_to_end(key);return entry[1]
   try:result=await asyncio.to_thread(_integration_get,path,params)
   except Exception as exc:
    if entry and now-entry[0]<DIRECTORY_CACHE_STALE_TTL:
     logger.warning("CatalogVR: использован устаревший кеш справочника %s после %s",path,type(exc).__name__)
     return entry[1]
    raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc
   directory_cache[key]=(time.monotonic(),result);directory_cache.move_to_end(key)
   while len(directory_cache)>DIRECTORY_CACHE_MAX_ENTRIES:directory_cache.popitem(last=False)
   return result
 finally:
  if not lock.locked() and directory_locks.get(key) is lock:directory_locks.pop(key,None)

async def get_product_filters():
 return await _cached_integration_get("integration/product-filters")

async def get_catalog_tree():
 return await _cached_integration_get("integration/catalog-tree")

async def get_catalog_brands(*,search:str="",page:int=1,page_size:int=100):
 return await _cached_integration_get("integration/brands",{"search":search,"page":page,"page_size":page_size})

async def get_catalog_tree():
 try:return await asyncio.to_thread(_integration_get,"integration/catalog-tree")
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc

async def get_catalog_brands(*,search:str="",page:int=1,page_size:int=100):
 try:return await asyncio.to_thread(_integration_get,"integration/brands",{"search":search,"page":page,"page_size":page_size})
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc

async def get_product_filter_options(filter_key:str,*,search:str="",page:int=1,page_size:int=100):
 return await _cached_integration_get(f"integration/product-filters/{quote(filter_key,safe='')}/options",{"search":search,"page":page,"page_size":page_size})

async def get_catalog_tree():
 global catalog_tree_cache
 if catalog_tree_cache and time.monotonic()-catalog_tree_cache[0]<CATALOG_TREE_CACHE_TTL:return catalog_tree_cache[1]
 try:payload=await asyncio.to_thread(_integration_get,"integration/catalog-tree")
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc
 if not isinstance(payload,list):raise VrCatalogError("vrcatalog вернул некорректное дерево каталога")
 catalog_tree_cache=(time.monotonic(),payload);return payload

async def get_brands(*,search:str="",page:int=1,page_size:int=100):
 try:return await asyncio.to_thread(_integration_get,"integration/brands",{"search":search,"page":page,"page_size":page_size})
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc

async def search_catalog_products(*,filters:dict,page:int=1,page_size:int=500,search:str="",sort_by:str|None=None,sort_dir:str|None=None):
 payload={"filters":filters,"page":page,"page_size":page_size}
 if search:payload["search"]=search
 if sort_by:payload["sort_by"]=sort_by
 if sort_dir:payload["sort_dir"]=sort_dir
 try:return await asyncio.to_thread(_integration_search,payload)
 except VrCatalogError:raise
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc

async def get_catalog_filter_keys(*,brands:list[str],sections:list[str]):
 """Получает все SKU для выбранных фасетов постранично, без N+1 запросов."""
 filters={}
 if sections:filters["section"]=sections
 if brands:filters["brand"]=brands
 if not filters:return None
 cache_key=tuple(sorted((key,tuple(sorted(set(values),key=str.casefold))) for key,values in filters.items()))
 now=time.monotonic();saved=catalog_filter_keys_cache.get(cache_key)
 if saved and now-saved[0]<CATALOG_FILTER_KEYS_CACHE_TTL:return saved[1]
 result=set();page=1
 while True:
  payload=await search_catalog_products(filters=filters,page=page,page_size=500,sort_by="name",sort_dir="asc")
  items=_source(payload)
  for item in items:
   if not isinstance(item,dict):continue
   for prefix in ("code","article"):
    key=_catalog_key(prefix,item.get(prefix))
    if key:result.add(key)
  pagination=_pagination(payload);pages=pagination.get("pages") or pagination.get("total_pages")
  if not items or pagination.get("has_next") is False or (pages is not None and page>=int(pages)) or len(items)<500:break
  page+=1
 catalog_filter_keys_cache[cache_key]=(now,result);return result

def _catalog_key(prefix:str,value):return f"{prefix}:{str(value).strip().lower()}" if value and str(value).strip() else None

def _drop_catalog_info(key):
 global _catalog_info_bytes
 catalog_info_cache.pop(key,None)
 _catalog_info_bytes-=_catalog_info_sizes.pop(key,0)

def _cache_catalog_info(key,item,now,size):
 global _catalog_info_bytes
 _drop_catalog_info(key)
 charge=size+sys.getsizeof(key)+256
 if charge>CATALOG_INFO_CACHE_MAX_BYTES:return
 catalog_info_cache[key]=(now,item);_catalog_info_sizes[key]=charge;_catalog_info_bytes+=charge
 while _catalog_info_bytes>CATALOG_INFO_CACHE_MAX_BYTES or len(catalog_info_cache)>CATALOG_INFO_CACHE_MAX_ENTRIES:
  _drop_catalog_info(next(iter(catalog_info_cache)))

def _prune_catalog_info_cache(now:float):
 global _catalog_info_bytes
 if not catalog_info_cache:
  _catalog_info_sizes.clear();_catalog_info_bytes=0
 expired=[key for key,(created_at,_) in catalog_info_cache.items() if now-created_at>=CATALOG_INFO_CACHE_TTL]
 for key in expired:_drop_catalog_info(key)
 while len(catalog_info_cache)>CATALOG_INFO_CACHE_MAX_ENTRIES:
  _drop_catalog_info(next(iter(catalog_info_cache)))

async def _finish_catalog_workers(tasks):
 await asyncio.gather(*tasks,return_exceptions=True)

async def get_catalog_batch_info(products):
 async with heavy_operation("catalog"):
  return await _get_catalog_batch_info(products)

def _prepare_catalog_batch_info(products):
 global catalog_info_cache

 unique={(_catalog_key("code",item.get("code")),_catalog_key("article",item.get("article"))):(item.get("code"),item.get("article")) for item in products if item.get("code") or item.get("article")}

 now=time.monotonic()
 _prune_catalog_info_cache(now)
 result={}
 missing=[]

 for (code_key,article_key),(code,article) in unique.items():
  cached=None

  for key in (code_key,article_key):
   if not key:continue
   entry=catalog_info_cache.get(key)
   if entry and now-entry[0]<CATALOG_INFO_CACHE_TTL:
    cached=entry[1]
    break

  cached_entry_found=any(
   key and (entry:=catalog_info_cache.get(key)) and now-entry[0]<CATALOG_INFO_CACHE_TTL
   for key in (code_key,article_key)
  )

  if cached_entry_found:
   if cached is not None:
    if code_key:result[code_key]=cached
    if article_key:result[article_key]=cached
  else:
   missing.append({"code":code,"article":article})

 del unique
 return now,result,missing

async def _get_catalog_batch_info(products):
 now,result,missing=await run_cpu(_prepare_catalog_batch_info,products)
 failed_batches=0;successful_batches=0
 consume_lock=threading.Lock()
 def consume(offset,batch,response,exc):
  nonlocal failed_batches,successful_batches
  with consume_lock:
   batch_number=offset//CATALOG_BATCH_SIZE+1
   if exc is not None:
    failed_batches+=1
    # Не логируем URL, payload и текст исключения: они могут содержать секреты.
    logger.warning("CatalogVR batch-info: пакет %s (%s товаров) не обработан: %s",batch_number,len(batch),type(exc).__name__)
    return

   successful_batches+=1
   returned_keys=set()
   for item in _source(response):
    if not isinstance(item,dict):continue
    nested=next((item.get(key) for key in ("product","catalog_product","catalogProduct","item") if isinstance(item.get(key),dict)),None)
    if nested:item={**item,**nested}
    image=catalog_item_image_url(item)
    if image:
     if not image.startswith("data:") and not urlparse(image).scheme:
      from app.config import settings
      image=_absolute_image_url(image,settings.vrcatalog_api_url)
     item={**item,"image_url":image}
    keys=(_catalog_key("code",item.get("code")),_catalog_key("article",item.get("article")))
    item_size=_bounded_size(item,CATALOG_INFO_CACHE_MAX_BYTES)
    for key in keys:
     if key:
      returned_keys.add(key);result[key]=item;_cache_catalog_info(key,item,now,item_size)

   # Отрицательно кешируем только результат успешно выполненного пакета.
   # Товары из упавшего запроса должны быть повторно запрошены при следующем отчёте.
   for requested in batch:
    code_key=_catalog_key("code",requested.get("code"));article_key=_catalog_key("article",requested.get("article"))
    if not any(key in returned_keys for key in (code_key,article_key) if key):
     if code_key:_cache_catalog_info(code_key,None,now,16)
     if article_key:_cache_catalog_info(article_key,None,now,16)

 async def load_batch(offset):
  batch=missing[offset:offset+CATALOG_BATCH_SIZE]
  try:response=await run_cpu(_integration_batch,{"products":batch})
  except Exception as exc:return offset,batch,None,exc
  return offset,batch,response,None

 max_workers=0
 # A bounded window preserves the old offset-order merge, including colliding
 # code/article aliases and negative-cache results, without keeping all replies.
 for start in range(0,len(missing),CATALOG_BATCH_SIZE*CATALOG_BATCH_CONCURRENCY):
  ensure_headroom()
  tasks=[asyncio.create_task(load_batch(offset)) for offset in range(start,min(len(missing),start+CATALOG_BATCH_SIZE*CATALOG_BATCH_CONCURRENCY),CATALOG_BATCH_SIZE)]
  max_workers=max(max_workers,len(tasks))
  try:
   for task in tasks:
    record=await task
    await run_cpu(consume,*record)
    del record
  finally:
   for task in tasks:
    if not task.done():task.cancel()
   cleanup=asyncio.create_task(_finish_catalog_workers(tasks))
   cancelled_during_cleanup=False
   while not cleanup.done():
    try:await asyncio.shield(cleanup)
    except asyncio.CancelledError:cancelled_during_cleanup=True
   cleanup.result()
   if cancelled_during_cleanup:raise asyncio.CancelledError
  del task,tasks,cleanup
 diagnostics("catalog_batches", batch_size=CATALOG_BATCH_SIZE, batch_workers=max_workers, requested_products=len(missing), catalog_keys=len(result), failed_batches=failed_batches, catalog_cache_charged_bytes=_catalog_info_bytes)
 _prune_catalog_info_cache(now)

 if failed_batches and not successful_batches and not result:
  raise VrCatalogError(f"CatalogVR не обработал {failed_batches} batch-пакетов")

 return result

async def get_catalog_category_map(products:list[dict]):
 """Возвращает найденные товары, включая товары с пустой категорией.

 Отсутствие ключа в результате означает, что товар не найден. Такой результат
 также кешируется, поэтому повторный отчёт не обращается к CatalogVR.
 """
 unique={(_catalog_key("code",item.get("code")),_catalog_key("article",item.get("article"))):(item.get("code"),item.get("article")) for item in products if item.get("code") or item.get("article")}
 if len(unique)>CATALOG_CATEGORY_MAP_LIMIT:raise VrCatalogError(f"Нельзя проверить более {CATALOG_CATEGORY_MAP_LIMIT} товаров за один запрос")

 now=time.monotonic();result={};missing=[]
 for (code_key,article_key),(code,article) in unique.items():
  keys=tuple(key for key in (code_key,article_key) if key)
  cached=next((catalog_category_cache[key][1] for key in keys if key in catalog_category_cache and now-catalog_category_cache[key][0]<CATALOG_CATEGORY_CACHE_TTL),None)
  known_missing=all(key in catalog_category_negative_cache and now-catalog_category_negative_cache[key]<CATALOG_CATEGORY_CACHE_TTL for key in keys)
  if cached is not None:
   for key in keys:result[key]=cached
  elif known_missing:
   continue
  else:missing.append({"code":code,"article":article})

 if not missing:return result
 try:response=await asyncio.to_thread(_integration_category_map,{"products":missing})
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc
 if not isinstance(response,dict) or not isinstance(response.get("items"),list):
  raise VrCatalogError("vrcatalog вернул некорректную карту категорий")

 returned_keys=set()
 for item in response["items"]:
  if not isinstance(item,dict):continue
  keys=tuple(key for key in (_catalog_key("code",item.get("code")),_catalog_key("article",item.get("article"))) if key)
  for key in keys:
   returned_keys.add(key);result[key]=item;catalog_category_cache[key]=(now,item)
   catalog_category_negative_cache.pop(key,None)

 for requested in missing:
  keys=tuple(key for key in (_catalog_key("code",requested.get("code")),_catalog_key("article",requested.get("article"))) if key)
  if not any(key in returned_keys for key in keys):
   for key in keys:catalog_category_negative_cache[key]=now
 return result

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
 if wanted_keys.issubset(values):return {key:values[key] for key in wanted_keys}
 try:
  for wanted in wanted_keys-values.keys():
   _,search=wanted.split(":",1);payload=await search_catalog_products(filters={},search=search,page=1,page_size=50);page_images=catalog_product_images(payload)
   if any(not urlparse(value).scheme and not value.startswith("data:") for value in page_images.values()):
    from app.config import settings
    page_images=catalog_product_images(payload,settings.vrcatalog_api_url)
   if wanted in page_images:values[wanted]=page_images[wanted]
 except Exception as exc:raise VrCatalogError(f"vrcatalog недоступен: {exc}") from exc
 image_cache=(time.monotonic(),values);return {key:values[key] for key in wanted_keys if key in values}

def is_horeca(keys:set[str],article,code):
 return any(value and f"{prefix}:{str(value).strip().lower()}" in keys for prefix,value in (("article",article),("code",code)))
