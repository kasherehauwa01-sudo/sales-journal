import asyncio
import pytest
from app.services import vrcatalog
from app.services.vrcatalog import VrCatalogError,horeca_keys,is_horeca

def setup_function():
 vrcatalog.cache=None;vrcatalog.image_cache=None
 vrcatalog.catalog_info_cache.clear();vrcatalog.catalog_category_cache.clear();vrcatalog.catalog_category_negative_cache.clear()

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
 payload={"items":[{"code":"1","image_url":"https://img/1.jpg"},{"article":"A2","images":[{"url":"https://img/2.jpg"}]},{"code":"3","photos":[{"path":"media/3.jpg"}]}]}
 assert vrcatalog.catalog_product_images(payload,"https://catalog.example/api")=={"code:1":"https://img/1.jpg","article:a2":"https://img/2.jpg","code:3":"https://catalog.example/api/media/3.jpg"}

def test_catalog_image_absolute_path_is_resolved_against_catalog_api():
 assert vrcatalog.catalog_product_images({"items":[{"code":"1","main_photo_url":"/media/1.jpg"}]},"https://catalog.example/vr/catalog/api")=={"code:1":"https://catalog.example/media/1.jpg"}

def test_catalog_images_are_loaded_in_pages_and_cached(monkeypatch):
 calls=[]
 async def search(**kwargs):
  calls.append(kwargs["page"]);return {"items":[{"code":"1","photo":"https://img/1.jpg"}],"total":1}
 monkeypatch.setattr(vrcatalog,"search_catalog_products",search)
 assert asyncio.run(vrcatalog.get_catalog_images({"code:1"}))=={"code:1":"https://img/1.jpg"}
 assert asyncio.run(vrcatalog.get_catalog_images({"code:1"}))=={"code:1":"https://img/1.jpg"}
 assert calls==[1]

def test_catalog_filter_metadata_and_options_use_integration_api(monkeypatch):
 calls=[]
 def request(path,params=None):
  calls.append((path,params));return {"items":["HoReCa"]}
 monkeypatch.setattr(vrcatalog,"_integration_get",request)
 assert asyncio.run(vrcatalog.get_product_filters())=={"items":["HoReCa"]}
 assert asyncio.run(vrcatalog.get_product_filter_options("property:HoReCa",search="hor",page=2,page_size=50))=={"items":["HoReCa"]}
 assert calls==[("integration/product-filters",None),("integration/product-filters/property%3AHoReCa/options",{"search":"hor","page":2,"page_size":50})]

def test_catalog_search_keeps_image_url_and_pagination(monkeypatch):
 payload={"items":[{"id":1,"code":"001","article":"A1","name":"Товар","image_url":"https://catalog/image.jpg","properties":[]}],"total":1,"page":1,"page_size":50,"pages":1}
 monkeypatch.setattr(vrcatalog,"_integration_search",lambda request:payload)
 assert asyncio.run(vrcatalog.search_catalog_products(filters={"brand":["VR"]},search="товар",page=1,page_size=50))==payload

def test_catalog_batch_info_is_one_request_and_maps_code_and_article(monkeypatch):
 calls=[]
 def batch(payload):
  calls.append(payload);return {"items":[{"code":" A-1 ","article":"ART-1","name":"Товар","horeca":False,"image_url":"https://img/1.jpg"}]}
 monkeypatch.setattr(vrcatalog,"_integration_batch",batch)
 result=asyncio.run(vrcatalog.get_catalog_batch_info([{"code":"A-1","article":"ART-1"},{"code":"A-1","article":"ART-1"}]))
 assert result["code:a-1"]["image_url"]=="https://img/1.jpg"
 assert result["article:art-1"]["horeca"] is False
 assert calls==[{"products":[{"code":"A-1","article":"ART-1"}]}]

def test_catalog_batch_info_unwraps_product_payload(monkeypatch):
 def batch(_payload):
  return {"items":[{"matched":True,"product":{"code":"A-2","article":"ART-2","category_name":"Посуда"}}]}
 monkeypatch.setattr(vrcatalog,"_integration_batch",batch)
 result=asyncio.run(vrcatalog.get_catalog_batch_info([{"code":"A-2","article":"ART-2"}]))
 assert result["code:a-2"]["category_name"]=="Посуда"

def test_catalog_batch_info_rejects_more_than_5000_products():
 with pytest.raises(VrCatalogError,match="5000"):
  asyncio.run(vrcatalog.get_catalog_batch_info([{"code":str(index)} for index in range(5001)]))

def test_catalog_category_map_uses_lightweight_endpoint_and_normalized_keys(monkeypatch):
 calls=[]
 def category_map(payload):
  calls.append(payload);return {"items":[{"code":" 123 ","article":" AbC ","category":"Посуда"}]}
 monkeypatch.setattr(vrcatalog,"_integration_category_map",category_map)
 result=asyncio.run(vrcatalog.get_catalog_category_map([{"code":"123","article":"ABC"}]))
 assert result["code:123"]["category"]=="Посуда"
 assert result["article:abc"]["category"]=="Посуда"
 assert calls==[{"products":[{"code":"123","article":"ABC"}]}]

def test_catalog_category_map_caches_category_null_as_positive(monkeypatch):
 calls=0
 def category_map(_payload):
  nonlocal calls;calls+=1;return {"items":[{"code":"1","category":None}]}
 monkeypatch.setattr(vrcatalog,"_integration_category_map",category_map)
 first=asyncio.run(vrcatalog.get_catalog_category_map([{"code":" 1 "}]))
 second=asyncio.run(vrcatalog.get_catalog_category_map([{"code":"1"}]))
 assert "code:1" in first and first["code:1"]["category"] is None
 assert "code:1" in second and calls==1

def test_catalog_category_map_negative_cache_avoids_repeated_request(monkeypatch):
 calls=0
 def category_map(_payload):
  nonlocal calls;calls+=1;return {"items":[]}
 monkeypatch.setattr(vrcatalog,"_integration_category_map",category_map)
 assert asyncio.run(vrcatalog.get_catalog_category_map([{"code":"missing","article":"NONE"}]))=={}
 assert asyncio.run(vrcatalog.get_catalog_category_map([{"code":" MISSING ","article":"none"}]))=={}
 assert calls==1

def test_catalog_category_map_does_not_hide_timeout(monkeypatch):
 monkeypatch.setattr(vrcatalog,"_integration_category_map",lambda _payload:(_ for _ in ()).throw(TimeoutError("timed out")))
 with pytest.raises(VrCatalogError,match="vrcatalog недоступен"):
  asyncio.run(vrcatalog.get_catalog_category_map([{"code":"1"}]))

def test_catalog_category_map_rejects_incomplete_response_shape(monkeypatch):
 monkeypatch.setattr(vrcatalog,"_integration_category_map",lambda _payload:{"data":[]})
 with pytest.raises(VrCatalogError,match="некорректную карту"):
  asyncio.run(vrcatalog.get_catalog_category_map([{"code":"1"}]))
