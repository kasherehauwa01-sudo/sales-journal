def aggregate_manager_sales(rows,client_managers:dict[str,str]):
 """Объединяет уже агрегированные SQL-данные клиентов по их менеджерам."""
 totals={}
 for client,revenue,sales_count in rows:
  manager=client_managers.get((client or "").strip().lower(),"Нет менеджера")
  current=totals.setdefault(manager,{"manager":manager,"revenue":0.0,"sales_count":0})
  current["revenue"]+=float(revenue or 0);current["sales_count"]+=int(sales_count or 0)
 return sorted(totals.values(),key=lambda item:(-item["revenue"],item["manager"].lower()))

def aggregate_buyer_type_dynamics(rows,client_types:dict[str,str]):
 """Строит динамические серии графика по видам покупателей из Clients."""
 labels=sorted({client_types.get((row.client or "").strip().lower(),"Не указан") for row in rows})
 sections=[{"key":f"buyer_{index}","label":label} for index,label in enumerate(labels)];keys={item["label"]:item["key"] for item in sections};periods={}
 for row in rows:
  period=row.period.date();entry=periods.setdefault(period,{"period":period,"revenue":0.0,"sales_count":0});label=client_types.get((row.client or "").strip().lower(),"Не указан");key=keys[label]
  entry[key]=entry.get(key,0)+float(row.revenue or 0);entry[f"{key}_checks"]=entry.get(f"{key}_checks",0)+int(row.checks or 0);entry["revenue"]+=float(row.revenue or 0);entry["sales_count"]+=int(row.checks or 0)
 return {"sections":sections,"items":list(periods.values())}
