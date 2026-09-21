from fastapi import APIRouter,Depends,HTTPException,Response
from pydantic import BaseModel,Field
from sqlalchemy import delete,select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import Scenario,ScenarioRun,SmtpConfig
from app.services.scenarios import send_test

router=APIRouter(tags=["Настройки"])
class SmtpIn(BaseModel):host:str;port:int=Field(ge=1,le=65535);security:str;username:str;password:str="";sender_email:str;sender_name:str
class TestEmail(BaseModel):email:str=Field(min_length=3,max_length=255)
class ScenarioIn(BaseModel):name:str;email:str;manager:str="Трошина Лариса";message_text:str="";enabled:bool=True
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
 return [{"id":row.id,"run_date":row.run_date,"status":row.status,"message":row.message} for row in rows]
@router.get("/scenarios")
async def scenarios(db:AsyncSession=Depends(get_db)):return (await db.scalars(select(Scenario).order_by(Scenario.id))).all()
@router.put("/scenarios/{scenario_id}")
async def update_scenario(scenario_id:int,data:ScenarioIn,db:AsyncSession=Depends(get_db)):
 row=await db.get(Scenario,scenario_id)
 if not row:raise HTTPException(404,"Сценарий не найден")
 for key,value in data.model_dump().items():setattr(row,key,value)
 await db.commit();await db.refresh(row);return row
@router.delete("/scenarios/{scenario_id}",status_code=204)
async def remove_scenario(scenario_id:int,db:AsyncSession=Depends(get_db)):
 await db.execute(delete(Scenario).where(Scenario.id==scenario_id));await db.commit();return Response(status_code=204)
