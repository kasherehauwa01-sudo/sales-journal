def aggregate_manager_sales(rows,client_managers:dict[str,str]):
 """Объединяет уже агрегированные SQL-данные клиентов по их менеджерам."""
 totals={}
 for client,revenue,sales_count in rows:
  manager=client_managers.get((client or "").strip().lower(),"Нет менеджера")
  current=totals.setdefault(manager,{"manager":manager,"revenue":0.0,"sales_count":0})
  current["revenue"]+=float(revenue or 0);current["sales_count"]+=int(sales_count or 0)
 return sorted(totals.values(),key=lambda item:(-item["revenue"],item["manager"].lower()))
