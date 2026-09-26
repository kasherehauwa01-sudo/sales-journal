from datetime import date


UNCATEGORIZED = "Без категории"
NOT_FOUND = "Не найдено в CatalogVR"


def percent_change(current: float, previous: float) -> float | None:
    return None if previous == 0 else (current - previous) / previous * 100


def stock_months(stock: float | None, monthly_units: float) -> float | None:
    if stock is None or monthly_units <= 0:
        return None
    return stock / monthly_units


def classify_candidate(row: dict, settings: dict, analysis_end: date) -> dict:
    """Добавляет прозрачные причины и стоп-факторы без единого скрытого score."""
    last_units=float(row.get("units_30",0));previous_units=float(row.get("units_previous_30",0))
    change=percent_change(last_units,previous_units);row["units_change"]=last_units-previous_units;row["units_change_percent"]=change
    row["revenue_change"]=float(row.get("revenue_30",0))-float(row.get("revenue_previous_30",0))
    row["revenue_change_percent"]=percent_change(float(row.get("revenue_30",0)),float(row.get("revenue_previous_30",0)))
    last_sale=row.get("last_sale");row["days_without_sales"]=(analysis_end-last_sale).days if last_sale else None
    months=max(float(row.get("analysis_days",0))/30,1);row["monthly_units"]=float(row.get("units",0))/months
    row["stock_months"]=stock_months(row.get("stock"),row["monthly_units"])
    reasons=[];stops=[]
    if previous_units>=settings["min_previous_units"] and change is not None and change<=settings["decline_percent"]:reasons.append("Продажи падают")
    if (row.get("stock") or 0)>0 and row["days_without_sales"] is not None and row["days_without_sales"]>=settings["stale_days"]:reasons.append("Давно не продавался")
    if (row.get("stock") or 0)>0 and last_units<=settings["low_sales_units"]:reasons.append("Низкие продажи при наличии остатка")
    if row["stock_months"] is not None and row["stock_months"]>=settings["excess_stock_months"]:reasons.append("Избыточный запас")
    if row.get("analysis_days",0)>=90 and row["monthly_units"]>0 and last_units<row["monthly_units"]*.6:reasons.append("Раньше продавался лучше")
    category_change=row.get("category_change_percent");row["category_deviation_pp"]=change-category_change if change is not None and category_change is not None else None
    if change is not None and category_change is not None and change<0 and row["category_deviation_pp"]<=-20:reasons.append("Падает сильнее категории")
    if row.get("analysis_days",0)<90:stops.append("Недостаточно истории")
    if change is not None and change>=30:stops.append("Продажи растут без скидки")
    if row.get("stock") is not None and row["stock"]<=0:stops.append("Нет в наличии")
    elif row.get("stock") is not None and row["stock"]<settings["min_stock"]:stops.append("Маленький остаток")
    row["reasons"]=reasons;row["stop_factors"]=stops;row["status"]="Не рекомендуется сейчас" if stops else "Кандидат" if reasons else "Нет сигналов"
    return row
