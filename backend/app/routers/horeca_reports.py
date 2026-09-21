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
from app.services.scenarios import _send

router=APIRouter(prefix="/reports/horeca",tags=["Отчеты HoReCa"])
class SendSelection(BaseModel):keys:list[str]=Field(min_length=1,max_length=10000)

async def _report(token:str,db):
 report=await db.scalar(select(HorecaReport).where(HorecaReport.token==token))
 if not report:raise HTTPException(404,"Отчет не найден или ссылка недействительна")
 return report

@router.get("/{token}")
async def get_report(token:str,db:AsyncSession=Depends(get_db)):
 report=await _report(token,db)
 return {"period_start":report.period_start,"period_end":report.period_end,"products":report.products}

@router.post("/{token}/send")
async def send_report(token:str,data:SendSelection,db:AsyncSession=Depends(get_db)):
 report=await _report(token,db);scenario=await db.get(Scenario,report.scenario_id);smtp=await db.get(SmtpConfig,1)
 if not scenario or not smtp:raise HTTPException(409,"Настройки сценария или SMTP не найдены")
 selected=set(data.keys);products=[item for item in report.products if item.get("key") in selected]
 if not products:raise HTTPException(422,"Не выбраны товары для отправки")
 try:recipients=parse_recipient_emails(scenario.reply_emails)
 except ValueError as exc:raise HTTPException(422,str(exc)) from exc
 book=Workbook();sheet=book.active;sheet.title="Товары";sheet["A1"]="Наименование"
 for index,item in enumerate(products,2):sheet.cell(index,1,item.get("name") or item.get("code") or item.get("article"))
 sheet.freeze_panes="A2";sheet.auto_filter.ref=f"A1:A{len(products)+1}";sheet.column_dimensions["A"].width=60;output=BytesIO();book.save(output)
 try:await asyncio.to_thread(_send,smtp,recipients,"Товары HoReCa для ОМиР",f"Выбрано товаров: {len(products)}",output.getvalue())
 except Exception as exc:raise HTTPException(502,f"Не удалось отправить письмо: {exc}") from exc
 return {"sent":True,"products":len(products),"recipients":len(recipients)}
