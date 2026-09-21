import asyncio
import pytest
from app.services import vrcatalog
from app.services.vrcatalog import VrCatalogError,horeca_keys,is_horeca

def setup_function():vrcatalog.cache=None;vrcatalog.image_cache=None

def test_horeca_products_are_detected_by_article_and_code():
 payload={"items":[{"article":" A-1 ","code":"001","properties":{"HoReCa":"HoReCa"}},{"article":"A-2","properties":{"HoReCa":"Нет"}}]}
 keys=horeca_keys(payload)
 assert is_horeca(keys,"a-1",None)
 assert is_horeca(keys,None,"001")
 assert not is_horeca(keys,"A-2",None)

def test_horeca_property_list_is_supported():
 keys=horeca_keys([{"sku":"SKU-7","attributes":[{"name":"HoReCa","value":"HoReCa"}]}])
 assert is_horeca(keys,"sku-7",None)

def test_horeca_one_page_collects_code_article_and_combined_product(monkeypatch):
 calls=[]
 async def search(**kwargs):
  calls.append(kwargs);return {"items":[{"code":" 001 "},{"article":" ART-2 "},{"code":"003","article":"ART-3"}],"total":3,"page":1,"pages":1}
 monkeypatch.setattr(vrcatalog,"search_catalog_products",search)
 keys=asyncio.run(vrcatalog.get_horeca_keys())
 assert keys=={"code:001","article:art-2","code:003","article:art-3"}
 assert calls==[{"filters":{"property:HoReCa":["HoReCa"]},"page":1,"page_size":500}]

def test_horeca_loads_multiple_pages_and_stops_at_total(monkeypatch):
 calls=[]
 async def search(**kwargs):
  calls.append(kwargs["page"])
  return {"data":{"items":[{"code":str(kwargs["page"])}]*500,"total":1000,"page":kwargs["page"],"total_pages":2}}
 monkeypatch.setattr(vrcatalog,"search_catalog_products",search)
 assert asyncio.run(vrcatalog.get_horeca_keys())=={"code:1","code:2"}
 assert calls==[1,2]

def test_horeca_empty_page_stops_pagination(monkeypatch):
 calls=[]
 async def search(**kwargs):calls.append(kwargs["page"]);return {"items":[],"total":0}
 monkeypatch.setattr(vrcatalog,"search_catalog_products",search)
 assert asyncio.run(vrcatalog.get_horeca_keys())==set()
 assert calls==[1]

def test_horeca_short_page_stops_without_extra_request(monkeypatch):
 calls=[]
 async def search(**kwargs):calls.append(kwargs["page"]);return {"items":[{"article":"A"}]}
 monkeypatch.setattr(vrcatalog,"search_catalog_products",search)
 assert asyncio.run(vrcatalog.get_horeca_keys())=={"article:a"}
 assert calls==[1]

def test_horeca_cache_is_reused(monkeypatch):
 calls=0
 async def search(**_kwargs):
  nonlocal calls;calls+=1;return {"items":[{"code":"1"}],"total":1}
 monkeypatch.setattr(vrcatalog,"search_catalog_products",search)
 assert asyncio.run(vrcatalog.get_horeca_keys())=={"code:1"}
 assert asyncio.run(vrcatalog.get_horeca_keys())=={"code:1"}
 assert calls==1

def test_catalog_error_is_wrapped(monkeypatch):
 async def search(**_kwargs):raise TimeoutError("read timed out")
 monkeypatch.setattr(vrcatalog,"search_catalog_products",search)
 with pytest.raises(VrCatalogError,match="vrcatalog недоступен"):
  asyncio.run(vrcatalog.get_horeca_keys())

def test_catalog_images_support_common_image_shapes():
 payload={"items":[{"code":"1","image_url":"https://img/1.jpg"},{"article":"A2","images":[{"url":"https://img/2.jpg"}]}]}
 assert vrcatalog.catalog_product_images(payload)=={"code:1":"https://img/1.jpg","article:a2":"https://img/2.jpg"}

def test_catalog_images_are_loaded_in_pages_and_cached(monkeypatch):
 calls=[]
 async def search(**kwargs):
  calls.append(kwargs["page"]);return {"items":[{"code":"1","photo":"https://img/1.jpg"}],"total":1}
 monkeypatch.setattr(vrcatalog,"search_catalog_products",search)
 assert asyncio.run(vrcatalog.get_catalog_images({"code:1"}))=={"code:1":"https://img/1.jpg"}
 assert asyncio.run(vrcatalog.get_catalog_images({"code:1"}))=={"code:1":"https://img/1.jpg"}
 assert calls==[1]
