from datetime import datetime
from fastapi import APIRouter,BackgroundTasks,Depends,HTTPException
from pydantic import BaseModel,Field
from typing import Literal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import AutoImportLog,FtpConfig
from app.services.ftp_autoload import run_autoload,test_connection

router=APIRouter(prefix="/ftp",tags=["FTP"])
class FtpInput(BaseModel):
 protocol:Literal["FTP","FTPS"]="FTP";host:str;port:int=Field(21,ge=1,le=65535);username:str;password:str="";directory:str="/";retries:int=Field(5,ge=1,le=20);retry_delay:int=Field(3,ge=0,le=300);enabled:bool=True
class FtpOut(BaseModel):
 protocol:str="FTP";host:str="";port:int=21;username:str="";directory:str="/";retries:int=5;retry_delay:int=3;enabled:bool=True;has_password:bool=False
class LogOut(BaseModel):
 id:int;filename:str|None;status:str;message:str|None;import_id:int|None;started_at:datetime;finished_at:datetime|None
 model_config={"from_attributes":True}

def output(config:FtpConfig|None):
 return FtpOut(**({"protocol":config.protocol,"host":config.host,"port":config.port,"username":config.username,"directory":config.directory,"retries":config.retries,"retry_delay":config.retry_delay,"enabled":config.enabled,"has_password":bool(config.password)} if config else {}))

@router.get("/settings",response_model=FtpOut)
async def get_settings(db:AsyncSession=Depends(get_db)):return output(await db.get(FtpConfig,1))

@router.put("/settings",response_model=FtpOut)
async def save_settings(payload:FtpInput,db:AsyncSession=Depends(get_db)):
 config=await db.get(FtpConfig,1)
 if not config:
  if not payload.password:raise HTTPException(422,"Укажите пароль FTP")
  config=FtpConfig(id=1,password=payload.password);db.add(config)
 for field in ("protocol","host","port","username","directory","retries","retry_delay","enabled"):setattr(config,field,getattr(payload,field))
 if payload.password:config.password=payload.password
 await db.commit();await db.refresh(config);return output(config)

@router.post("/test")
async def check(payload:FtpInput,db:AsyncSession=Depends(get_db)):
 saved=await db.get(FtpConfig,1);password=payload.password or (saved.password if saved else "")
 if not password:raise HTTPException(422,"Укажите пароль FTP")
 probe=FtpConfig(protocol=payload.protocol,host=payload.host,port=payload.port,username=payload.username,password=password,directory=payload.directory,retries=payload.retries,retry_delay=payload.retry_delay,enabled=True)
 try:count=await test_connection(probe)
 except Exception as exc:raise HTTPException(400,f"Ошибка подключения: {exc}")
 return {"ok":True,"files":count}

@router.post("/run",status_code=202)
async def run_now(background:BackgroundTasks):
 background.add_task(run_autoload);return {"ok":True}

@router.get("/history",response_model=list[LogOut])
async def history(db:AsyncSession=Depends(get_db)):return (await db.scalars(select(AutoImportLog).order_by(AutoImportLog.id.desc()).limit(200))).all()
