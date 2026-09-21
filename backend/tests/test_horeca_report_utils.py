from app.services.horeca_report_utils import build_horeca_products

def test_products_are_sorted_by_three_month_sales_and_horeca_is_excluded():
 current=[("A1","1","Первый",2),("A2","2","Исключить",5),("A3","3","Третий",1)]
 three=[("A1","1","Первый",10),("A2","2","Исключить",30),("A3","3","Третий",20)]
 result=build_horeca_products(current,three,{"code:2"})
 assert [item["name"] for item in result]==["Третий","Первый"]
 assert result[0]["period_units"]==1
 assert result[0]["three_month_units"]==20
