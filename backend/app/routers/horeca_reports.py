import asyncio
from io import BytesIO
from fastapi import APIRouter,Depends,HTTPException
from openpyxl import Workbook
from pydantic import BaseModel,Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import HorecaReport,Scenario,SmtpConfig
from app.services.email_recipients import parse_recipient_emails
from app.services.horeca_report_utils import omir_codes
from app.services.scenarios import _send
from app.services.vrcatalog import VrCatalogError,get_catalog_images

router=APIRouter(prefix="/reports/horeca",tags=["Отчеты HoReCa"])
class SendSelection(BaseModel):keys:list[str]=Field(min_length=1,max_length=10000)

async def _report(token:str,db):
 report=await db.scalar(select(HorecaReport).where(HorecaReport.token==token))
 if not report:raise HTTPException(404,"Отчет не найден или ссылка недействительна")
 return report

@router.get("/{token}")
async def get_report(token:str,db:AsyncSession=Depends(get_db)):
 report=await _report(token,db)
 products=[dict(item) for item in report.products];missing={item.get("key") for item in products if item.get("key") and not item.get("photo")}
 if missing:
  try:
   images=await get_catalog_images(missing)
   for item in products:
    if not item.get("photo"):item["photo"]=images.get(item.get("key"))
   report.products=products;await db.commit()
  except VrCatalogError:pass  # отчет остается доступным, даже если каталог временно недоступен
 return {"period_start":report.period_start,"period_end":report.period_end,"products":products}

@router.post("/{token}/send")
async def send_report(token:str,data:SendSelection,db:AsyncSession=Depends(get_db)):
 report=await _report(token,db);scenario=await db.get(Scenario,report.scenario_id);smtp=await db.get(SmtpConfig,1)
 if not scenario or not smtp:raise HTTPException(409,"Настройки сценария или SMTP не найдены")
 selected=set(data.keys);products=[item for item in report.products if item.get("key") in selected]
 if not products:raise HTTPException(422,"Не выбраны товары для отправки")
 try:recipients=parse_recipient_emails(scenario.reply_emails)
 except ValueError as exc:raise HTTPException(422,str(exc)) from exc
 book=Workbook();sheet=book.active;sheet.title="Товары"
 # Первая строка намеренно остается пустой согласно формату импорта ОМиР.
 for index,code in enumerate(omir_codes(products),2):sheet.cell(index,1,code)
 sheet.freeze_panes="A2";sheet.column_dimensions["A"].width=24;output=BytesIO();book.save(output)
 try:await asyncio.to_thread(_send,smtp,recipients,"Товары HoReCa для ОМиР",f"Выбрано товаров: {len(products)}",output.getvalue())
 except Exception as exc:raise HTTPException(502,f"Не удалось отправить письмо: {exc}") from exc
 return {"sent":True,"products":len(products),"recipients":len(recipients)}
