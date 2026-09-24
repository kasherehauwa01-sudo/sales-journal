from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException,Response
from pydantic import BaseModel,Field
from sqlalchemy import delete,select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import Scenario,ScenarioRun,SmtpConfig
from app.services.scenarios import send_test
from app.services.scenarios import run_scenario
from app.services.email_recipients import parse_recipient_emails
from app.services.scenario_periods import manual_test_period
from app.config import settings

router=APIRouter(tags=["Настройки"])
class SmtpIn(BaseModel):host:str;port:int=Field(ge=1,le=65535);security:str;username:str;password:str="";sender_email:str;sender_name:str
class TestEmail(BaseModel):email:str=Field(min_length=3,max_length=255)
class ScenarioIn(BaseModel):name:str;email:str;reply_emails:str="";manager:str="Трошина Лариса";message_text:str="";enabled:bool=True
def smtp_out(row):return {"host":row.host,"port":row.port,"security":row.security,"username":row.username,"password":"","sender_email":row.sender_email,"sender_name":row.sender_name,"has_password":bool(row.password)}
@router.get("/smtp/settings")
async def get_smtp(db:AsyncSession=Depends(get_db)):
 row=await db.get(SmtpConfig,1);return smtp_out(row) if row else None
@router.put("/smtp/settings")
async def save_smtp(data:SmtpIn,db:AsyncSession=Depends(get_db)):
 row=await db.get(SmtpConfig,1)
 if not row:row=SmtpConfig(id=1,password=data.password);db.add(row)
 for key,value in data.model_dump(exclude={"password"}).items():setattr(row,key,value)
 if data.password:row.password=data.password
 await db.commit();await db.refresh(row);return smtp_out(row)
@router.post("/smtp/test")
async def test_smtp(data:TestEmail,db:AsyncSession=Depends(get_db)):
 row=await db.get(SmtpConfig,1)
 if not row:raise HTTPException(400,"SMTP не настроен")
 if "@" not in data.email:raise HTTPException(400,"Введите корректный тестовый email")
 try:await send_test(row,data.email)
 except Exception as exc:raise HTTPException(502,f"Не удалось отправить письмо: {exc}") from exc
 return {"sent":True}
@router.get("/smtp/history")
async def smtp_history(db:AsyncSession=Depends(get_db)):
 rows=(await db.scalars(select(ScenarioRun).order_by(ScenarioRun.created_at.desc()).limit(200))).all()
 return [{"id":row.id,"run_date":row.run_date,"run_type":row.run_type,"period_start":row.period_start,"period_end":row.period_end,"recipients":row.recipients,"status":row.status,"message":row.message} for row in rows]
@router.get("/scenarios")
async def scenarios(db:AsyncSession=Depends(get_db)):return (await db.scalars(select(Scenario).order_by(Scenario.id))).all()
@router.post("/scenarios/{scenario_id}/test")
async def test_scenario(scenario_id:int,db:AsyncSession=Depends(get_db)):
 row=await db.get(Scenario,scenario_id)
 if not row:raise HTTPException(404,"Сценарий не найден")
 try:recipients=parse_recipient_emails(row.email)
 except ValueError as exc:raise HTTPException(422,str(exc)) from exc
 today=datetime.now(ZoneInfo(settings.autoload_timezone)).date();period=manual_test_period(today);run=ScenarioRun(scenario_id=row.id,run_date=today,run_type="manual_test",period_start=period[0],period_end=period[1],recipients="\n".join(recipients),status="running");db.add(run);await db.commit();await db.refresh(run)
 try:
  result=await run_scenario(row,today,period);run.status="completed";run.message="Тестовый отчет отправлен"
 except Exception as exc:
  run.status="failed";run.message=str(exc)[:4000];await db.commit();raise HTTPException(502,f"Не удалось отправить тестовый отчет: {exc}") from exc
 await db.commit();return {"sent":True,"date_from":result["date_from"],"date_to":result["date_to"],"recipients":result["recipients"]}
@router.put("/scenarios/{scenario_id}")
async def update_scenario(scenario_id:int,data:ScenarioIn,db:AsyncSession=Depends(get_db)):
 row=await db.get(Scenario,scenario_id)
 if not row:raise HTTPException(404,"Сценарий не найден")
 try:
  if data.email.strip():data.email="\n".join(parse_recipient_emails(data.email))
  elif data.enabled:raise ValueError("Укажите хотя бы один email получателя")
  if data.reply_emails.strip():data.reply_emails="\n".join(parse_recipient_emails(data.reply_emails))
  elif data.enabled:raise ValueError("Укажите хотя бы один email для обратного письма")
 except ValueError as exc:raise HTTPException(422,str(exc)) from exc
 for key,value in data.model_dump().items():setattr(row,key,value)
 await db.commit();await db.refresh(row);return row
@router.delete("/scenarios/{scenario_id}",status_code=204)
async def remove_scenario(scenario_id:int,db:AsyncSession=Depends(get_db)):
 await db.execute(delete(Scenario).where(Scenario.id==scenario_id));await db.commit();return Response(status_code=204)
