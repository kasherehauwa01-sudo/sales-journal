from datetime import timedelta

def normalize_identifier(value):return str(value).strip().lower() if value else ""
def product_key(article,code,name):return f"code:{normalize_identifier(code)}" if normalize_identifier(code) else f"article:{normalize_identifier(article)}" if normalize_identifier(article) else f"name:{normalize_identifier(name)}"
def previous_period(date_from,date_to):
 length=(date_to-date_from).days+1;return date_from-timedelta(days=length),date_from-timedelta(days=1)
def percent_change(current,previous):return None if not previous else (current-previous)/previous*100
