from datetime import date,timedelta

def report_period(day:date):
 if day.day==14:
  first=day.replace(day=1);return (first-timedelta(days=1)).replace(day=27),day.replace(day=13)
 if day.day==28:return day.replace(day=14),day.replace(day=27)
 return None
