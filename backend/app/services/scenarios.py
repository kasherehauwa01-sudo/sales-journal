import asyncio,secrets,smtplib
from datetime import date,datetime,time,timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo
from sqlalchemy import false,func,select
from app.config import settings
from app.database import SessionLocal
from app.models import HorecaReport,Sale,SaleItem,Scenario,ScenarioRun,SmtpConfig
from app.services.clients_vr import get_manager_clients
from app.services.scenario_periods import months_before,report_period
from app.services.vrcatalog import get_catalog_batch_info
from app.services.email_recipients import parse_recipient_emails
from app.services.horeca_report_utils import build_horeca_products_from_info,limit_horeca_products

def _send(config:SmtpConfig,to:str|list[str],subject:str,body:str,attachment:bytes|None=None):
 recipients=[to] if isinstance(to,str) else to
 message=EmailMessage();message["Subject"]=subject;message["From"]=f"{config.sender_name} <{config.sender_email}>";message["To"]=", ".join(recipients);message.set_content(body)
 if attachment:message.add_attachment(attachment,maintype="application",subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",filename="Продажи HoReCa.xlsx")
 client=smtplib.SMTP_SSL(config.host,config.port,timeout=30) if config.security=="SSL" else smtplib.SMTP(config.host,config.port,timeout=30)
 try:
  if config.security=="STARTTLS":client.starttls()
  if config.username:client.login(config.username,config.password)
  client.send_message(message)
 finally:client.quit()

async def send_test(config:SmtpConfig,recipient:str):
 await asyncio.to_thread(_send,config,recipient,"Проверка SMTP","Тестовое сообщение Sales Journal")

async def run_scenario(scenario:Scenario,run_date:date,period_override:tuple[date,date]|None=None):
 period=period_override or report_period(run_date)
 if not period:return
 clients=await get_manager_clients(scenario.manager);normalized=[x.strip().lower() for x in clients]
 async with SessionLocal() as db:
  smtp=await db.get(SmtpConfig,1)
  if not smtp:raise RuntimeError("SMTP не настроен")
  client_filter=func.lower(func.trim(Sale.client)).in_(normalized) if normalized else false();group=(SaleItem.article,SaleItem.code,SaleItem.name)
  async def quantities(start,end):
   q=select(*group,func.sum(SaleItem.quantity).label("units")).join(Sale).where(Sale.sale_date.between(start,end),client_filter).group_by(*group)
   return (await db.execute(q)).all()
  current=await quantities(*period);three_start=months_before(period[1],3)+timedelta(days=1);three=await quantities(three_start,period[1]);catalog_info=await get_catalog_batch_info([{"article":row[0],"code":row[1]} for row in current]);products=limit_horeca_products(build_horeca_products_from_info(current,three,catalog_info))
  report=HorecaReport(token=secrets.token_urlsafe(32),scenario_id=scenario.id,period_start=period[0],period_end=period[1],products=products);db.add(report);await db.commit();link=f"{settings.public_url.rstrip('/')}/reports/horeca/{report.token}";period_text=f"Период отчета: {period[0]:%d.%m.%Y}–{period[1]:%d.%m.%Y}"
  recipients=parse_recipient_emails(scenario.email);await asyncio.to_thread(_send,smtp,recipients,"Продажи HoReCa",f"{scenario.message_text.strip()}\n\n{period_text}\n\nОткрыть перечень товаров: {link}".strip())
  return {"date_from":period[0],"date_to":period[1],"recipients":recipients,"link":link,"products":len(products)}

async def scheduler():
 zone=ZoneInfo(settings.autoload_timezone)
 while True:
  today=datetime.now(zone).date()
  if report_period(today):
   async with SessionLocal() as db:
    scenarios=(await db.scalars(select(Scenario).where(Scenario.enabled.is_(True)))).all()
    for scenario in scenarios:
     exists=await db.scalar(select(ScenarioRun.id).where(ScenarioRun.scenario_id==scenario.id,ScenarioRun.run_date==today,ScenarioRun.run_type=="scheduled"))
     if exists:continue
     period=report_period(today);run=ScenarioRun(scenario_id=scenario.id,run_date=today,run_type="scheduled",period_start=period[0],period_end=period[1],recipients=scenario.email,status="running");db.add(run);await db.commit()
     try:await run_scenario(scenario,today,period);run.status="completed";run.message="Отчет отправлен"
     except Exception as exc:run.status="failed";run.message=str(exc)[:4000]
     await db.commit()
  target=datetime.combine(today+timedelta(days=1),time(0,5),zone);await asyncio.sleep((target-datetime.now(zone)).total_seconds())
