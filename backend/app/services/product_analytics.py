from datetime import date, timedelta

GROUP_FIELDS = {"product", "brand", "manufacturer", "category", "subcategory", "material"}
PROPERTY_ALIASES = {
    "brand": ("brand", "Бренд"), "manufacturer": ("manufacturer", "Производитель"),
    "category": ("category", "Категория"), "subcategory": ("subcategory", "Подкатегория"),
    "material": ("material", "Материал"),
}


def previous_period(start: date, end: date) -> tuple[date, date]:
    length = (end - start).days + 1
    return start - timedelta(days=length), start - timedelta(days=1)


def product_key(article, code, name="") -> str:
    if code and str(code).strip(): return f"code:{str(code).strip().casefold()}"
    if article and str(article).strip(): return f"article:{str(article).strip().casefold()}"
    return f"unknown:{str(name).strip().casefold()}"


def percent_change(current: float, previous: float):
    return None if previous == 0 else (current - previous) / previous * 100


def catalog_property(product: dict, field: str) -> str:
    aliases = PROPERTY_ALIASES.get(field, (field,))
    for key in aliases:
        if product.get(key): return str(product[key]).strip()
    properties = product.get("properties") or product.get("attributes") or {}
    if isinstance(properties, dict):
        for key in aliases:
            if properties.get(key): return str(properties[key]).strip()
    if isinstance(properties, list):
        for item in properties:
            if not isinstance(item, dict): continue
            name = str(item.get("name") or item.get("property") or "").strip().casefold()
            if any(name == alias.casefold() for alias in aliases): return str(item.get("value") or "").strip() or "Не заполнено"
    return "Не заполнено"


def merge_periods(current: list[dict], previous: list[dict], catalog: dict[str, dict]) -> list[dict]:
    current_by = {x["key"]: x for x in current}; previous_by = {x["key"]: x for x in previous}; result = []
    for key in current_by.keys() | previous_by.keys():
        now, old = current_by.get(key, {}), previous_by.get(key, {}); info = catalog.get(key, {})
        revenue, old_revenue = float(now.get("revenue", 0)), float(old.get("revenue", 0)); units, old_units = float(now.get("units", 0)), float(old.get("units", 0)); checks, old_checks = int(now.get("checks", 0)), int(old.get("checks", 0))
        result.append({"key": key, "article": now.get("article") or old.get("article"), "code": now.get("code") or old.get("code"), "name": now.get("name") or old.get("name") or info.get("name") or "Без названия", "brand": catalog_property(info,"brand"), "manufacturer": catalog_property(info,"manufacturer"), "category": catalog_property(info,"category"), "subcategory": catalog_property(info,"subcategory"), "material": catalog_property(info,"material"), "image_url": info.get("image_url"), "revenue": revenue, "previous_revenue": old_revenue, "revenue_difference": revenue-old_revenue, "revenue_change": percent_change(revenue,old_revenue), "units": units, "previous_units": old_units, "units_difference": units-old_units, "checks": checks, "previous_checks": old_checks, "checks_change": percent_change(checks,old_checks), "average_price": revenue/units if units else 0, "first_sale": now.get("first_sale"), "last_sale": now.get("last_sale") or old.get("last_sale")})
    return result


def classify(rows: list[dict]) -> dict[str, list[dict]]:
    return {"growth": [x for x in rows if x["previous_revenue"] > 0 and x["revenue"] > x["previous_revenue"]], "decline": [x for x in rows if x["revenue"] > 0 and x["previous_revenue"] > 0 and x["revenue"] < x["previous_revenue"]], "stopped": [x for x in rows if x["previous_revenue"] > 0 and x["revenue"] == 0], "new": [x for x in rows if x["revenue"] > 0 and x["previous_revenue"] == 0]}


def group_rows(rows: list[dict], group_by: str) -> list[dict]:
    if group_by == "product": return rows
    totals = {}
    for row in rows:
        label = row.get(group_by) or "Не заполнено"; item = totals.setdefault(label,{"key":f"{group_by}:{label}","name":label,"article":None,"code":None,"brand":row.get("brand"),"manufacturer":row.get("manufacturer"),"category":row.get("category"),"subcategory":row.get("subcategory"),"material":row.get("material"),"revenue":0.0,"previous_revenue":0.0,"units":0.0,"previous_units":0.0,"checks":0,"previous_checks":0,"sku":0})
        for key in ("revenue","previous_revenue","units","previous_units","checks","previous_checks"): item[key]+=row[key]
        item["sku"]+=1
    for item in totals.values(): item.update(revenue_difference=item["revenue"]-item["previous_revenue"],revenue_change=percent_change(item["revenue"],item["previous_revenue"]),units_difference=item["units"]-item["previous_units"],checks_change=percent_change(item["checks"],item["previous_checks"]),average_price=item["revenue"]/item["units"] if item["units"] else 0)
    return list(totals.values())


def summary(rows: list[dict]) -> dict:
    active=[x for x in rows if x["revenue"]!=0]; old=[x for x in rows if x["previous_revenue"]!=0]; groups=classify(rows); revenue=sum(x["revenue"] for x in rows); previous_revenue=sum(x["previous_revenue"] for x in rows); units=sum(x["units"] for x in rows); previous_units=sum(x["previous_units"] for x in rows)
    return {"revenue":{"current":revenue,"previous":previous_revenue,"change_percent":percent_change(revenue,previous_revenue)},"units":{"current":units,"previous":previous_units,"change_percent":percent_change(units,previous_units)},"checks":{"current":sum(x["checks"] for x in active),"previous":sum(x["previous_checks"] for x in old)},"sku":{"current":len(active),"previous":len(old)},"average_revenue_per_sku":revenue/len(active) if active else 0,"average_units_per_sku":units/len(active) if active else 0,"new":len(groups["new"]),"growth":len(groups["growth"]),"decline":len(groups["decline"]),"stopped":len(groups["stopped"])}
