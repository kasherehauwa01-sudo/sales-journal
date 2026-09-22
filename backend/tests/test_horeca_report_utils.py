from app.services.horeca_report_utils import build_horeca_products,limit_horeca_products,omir_codes

def test_products_are_sorted_by_three_month_sales_and_horeca_is_excluded():
 current=[("A1","1","Первый",2),("A2","2","Исключить",5),("A3","3","Третий",1)]
 three=[("A1","1","Первый",10),("A2","2","Исключить",30),("A3","3","Третий",20)]
 result=build_horeca_products(current,three,{"code:2"})
 assert [item["name"] for item in result]==["Третий","Первый"]
 assert result[0]["period_units"]==1
 assert result[0]["three_month_units"]==20

def test_omir_export_uses_selected_product_codes():
 assert omir_codes([{"code":"001","name":"Первый"},{"code":"ABC-2","name":"Второй"}])==["001","ABC-2"]

def test_horeca_selection_is_limited_to_first_300_products():
 products=[{"key":str(index)} for index in range(350)]
 assert limit_horeca_products(products)==products[:300]
