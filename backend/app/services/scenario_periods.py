from datetime import date,timedelta
import calendar

def report_period(day:date):
 if day.day==14:
  first=day.replace(day=1);return (first-timedelta(days=1)).replace(day=27),day.replace(day=13)
 if day.day==28:return day.replace(day=14),day.replace(day=27)
 return None

def months_before(day:date,months:int):
 total=day.year*12+day.month-1-months;year,month=divmod(total,12);month+=1
 return day.replace(year=year,month=month,day=min(day.day,calendar.monthrange(year,month)[1]))

def manual_test_period(today:date):return today-timedelta(days=13),today
