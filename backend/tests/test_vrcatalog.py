from app.services.vrcatalog import horeca_keys,is_horeca

def test_horeca_products_are_detected_by_article_and_code():
 payload={"items":[{"article":" A-1 ","code":"001","properties":{"HoReCa":"HoReCa"}},{"article":"A-2","properties":{"HoReCa":"Нет"}}]}
 keys=horeca_keys(payload)
 assert is_horeca(keys,"a-1",None)
 assert is_horeca(keys,None,"001")
 assert not is_horeca(keys,"A-2",None)

def test_horeca_property_list_is_supported():
 keys=horeca_keys([{"sku":"SKU-7","attributes":[{"name":"HoReCa","value":"HoReCa"}]}])
 assert is_horeca(keys,"sku-7",None)
