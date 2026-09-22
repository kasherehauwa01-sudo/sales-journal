from datetime import timedelta

SUMMARY_LABELS={
 "revenue":"Продажи, ₽",
 "units":"Продано, шт.",
 "checks":"Количество чеков",
 "clients":"Уникальных клиентов",
 "average_price":"Средняя цена продажи",
 "items_per_check":"Среднее количество единиц в чеке",
 "discount_amount":"Сумма скидки, ₽",
 "average_discount":"Средний процент скидки",
}

def normalize_identifier(value):return str(value).strip().lower() if value else ""
def product_key(article,code,name):return f"code:{normalize_identifier(code)}" if normalize_identifier(code) else f"article:{normalize_identifier(article)}" if normalize_identifier(article) else f"name:{normalize_identifier(name)}"
def previous_period(date_from,date_to):
 length=(date_to-date_from).days+1;return date_from-timedelta(days=length),date_from-timedelta(days=1)
def percent_change(current,previous):return None if not previous else (current-previous)/previous*100

def localized_summary_rows(summary):return [(SUMMARY_LABELS.get(key,key),value) for key,value in summary.items()]
