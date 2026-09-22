from datetime import datetime
from types import SimpleNamespace
from app.services.manager_analytics import aggregate_buyer_type_dynamics,aggregate_manager_sales

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

def test_dynamics_are_grouped_by_buyer_type():
 period=datetime(2026,9,22)
 rows=[SimpleNamespace(period=period,client=" Клиент А ",revenue=100,checks=2),SimpleNamespace(period=period,client="КЛИЕНТ Б",revenue=50,checks=1),SimpleNamespace(period=period,client="Без типа",revenue=25,checks=1)]
 result=aggregate_buyer_type_dynamics(rows,{"клиент а":"Розница","клиент б":"HoReCa"})
 assert result["sections"]==[{"key":"buyer_0","label":"HoReCa"},{"key":"buyer_1","label":"Не указан"},{"key":"buyer_2","label":"Розница"}]
 assert result["items"]==[{"period":period.date(),"revenue":175.0,"sales_count":4,"buyer_2":100.0,"buyer_2_checks":2,"buyer_0":50.0,"buyer_0_checks":1,"buyer_1":25.0,"buyer_1_checks":1}]
