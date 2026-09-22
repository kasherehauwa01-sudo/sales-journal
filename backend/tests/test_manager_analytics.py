from app.services.manager_analytics import aggregate_manager_sales

def test_sales_are_aggregated_by_normalized_client_manager():
 rows=[(" Клиент А ",1000,2),("КЛИЕНТ Б",500,1),("Неизвестный",200,1)]
 result=aggregate_manager_sales(rows,{"клиент а":"Менеджер 1","клиент б":"Менеджер 1"})
 assert result==[
  {"manager":"Менеджер 1","revenue":1500.0,"sales_count":3},
  {"manager":"Нет менеджера","revenue":200.0,"sales_count":1},
 ]

def test_managers_are_sorted_by_revenue_descending():
 result=aggregate_manager_sales([("А",10,1),("Б",20,2)],{"а":"Первый","б":"Второй"})
 assert [item["manager"] for item in result]==["Второй","Первый"]
