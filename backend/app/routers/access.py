import base64
from datetime import datetime,timezone
from uuid import uuid4

from fastapi import APIRouter,Depends,File,Header,HTTPException,Query,Request,Response,UploadFile
from pydantic import BaseModel,Field
from sqlalchemy import or_,select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AccessAuditEvent,AccessDevice
from app.services.access_devices import audit,effective_status,make_common_name
from app.services.mtls_helper import MtlsHelperError,helper_call
from app.services.settings_auth import require_settings_admin

router=APIRouter(prefix="/settings/access",tags=["Доступы"],dependencies=[Depends(require_settings_admin)])

class DeviceCreate(BaseModel):
 device_name:str=Field(min_length=2,max_length=255);owner:str|None=Field(None,max_length=255);comment:str|None=Field(None,max_length=4000);validity_years:int=Field(3,ge=1,le=5)
class DeviceUpdate(BaseModel):
 device_name:str=Field(min_length=2,max_length=255);owner:str|None=Field(None,max_length=255);comment:str|None=Field(None,max_length=4000)

def ip(request:Request):return request.client.host if request.client else None
def out(row:AccessDevice):
 return {"id":row.id,"device_name":row.device_name,"owner":row.owner,"comment":row.comment,"common_name":row.common_name,
  "serial_number":row.serial_number,"fingerprint":row.fingerprint,"created_at":row.created_at,"expires_at":row.expires_at,
  "status":effective_status(row),"last_used_at":row.last_used_at,"revoked_at":row.revoked_at,
  "package_available":row.package_available and bool(row.package_expires_at and row.package_expires_at>datetime.now(timezone.utc))}
def parsed(value:str):return datetime.fromisoformat(value.replace("Z","+00:00"))

async def issue(data:DeviceCreate,request:Request,db:AsyncSession):
 for _ in range(5):
  common_name=make_common_name(data.device_name)
  if not await db.scalar(select(AccessDevice.id).where(AccessDevice.common_name==common_name)):break
 else:raise HTTPException(409,"Не удалось сформировать уникальный CN")
 try:result=await helper_call("issue",request_id=uuid4().hex,common_name=common_name,validity_days=data.validity_years*365)
 except MtlsHelperError as exc:raise HTTPException(502,str(exc)) from exc
 row=AccessDevice(device_name=data.device_name.strip(),owner=(data.owner or "").strip() or None,comment=(data.comment or "").strip() or None,
  common_name=common_name,serial_number=result["serial_number"],fingerprint=result["fingerprint"],created_at=parsed(result["created_at"]),
  expires_at=parsed(result["expires_at"]),status="active",package_available=True,package_expires_at=parsed(result["package_expires_at"]))
 db.add(row);await db.flush();audit(db,row.id,"issued",ip(request),{"common_name":common_name});await db.commit();await db.refresh(row)
 return {"device":out(row),"download_token":result["download_token"],"password":result["password"]}

@router.get("/devices")
async def devices(search:str|None=None,status:str|None=Query(None,pattern="^(active|revoked|expired)$"),db:AsyncSession=Depends(get_db)):
 q=select(AccessDevice).where(AccessDevice.deleted_at.is_(None)).order_by(AccessDevice.created_at.desc())
 if search:q=q.where(or_(AccessDevice.device_name.ilike(f"%{search}%"),AccessDevice.common_name.ilike(f"%{search}%"),AccessDevice.owner.ilike(f"%{search}%")))
 rows=(await db.scalars(q)).all();result=[out(row) for row in rows]
 return [row for row in result if not status or row["status"]==status]

@router.post("/devices",status_code=201)
async def create_device(data:DeviceCreate,request:Request,db:AsyncSession=Depends(get_db)):return await issue(data,request,db)

@router.get("/devices/{device_id}")
async def device(device_id:int,db:AsyncSession=Depends(get_db)):
 row=await db.get(AccessDevice,device_id)
 if not row or row.deleted_at:raise HTTPException(404,"Устройство не найдено")
 return out(row)

@router.patch("/devices/{device_id}")
async def update_device(device_id:int,data:DeviceUpdate,request:Request,db:AsyncSession=Depends(get_db)):
 row=await db.get(AccessDevice,device_id)
 if not row or row.deleted_at:raise HTTPException(404,"Устройство не найдено")
 changes={key:value for key,value in data.model_dump().items() if getattr(row,key)!=value}
 for key,value in data.model_dump().items():setattr(row,key,value.strip() if isinstance(value,str) else value)
 audit(db,row.id,"updated",ip(request),{"fields":sorted(changes)});await db.commit();await db.refresh(row);return out(row)

async def revoke(row:AccessDevice,request:Request,db:AsyncSession):
 try:await helper_call("revoke",serial_number=row.serial_number)
 except MtlsHelperError as exc:raise HTTPException(502,str(exc)) from exc
 if row.status!="revoked":
  row.status="revoked";row.revoked_at=datetime.now(timezone.utc)
  audit(db,row.id,"revoked",ip(request),{"serial_number":row.serial_number})
 row.package_available=False;await db.commit()

@router.post("/devices/{device_id}/revoke")
async def revoke_device(device_id:int,request:Request,db:AsyncSession=Depends(get_db)):
 row=await db.get(AccessDevice,device_id)
 if not row or row.deleted_at:raise HTTPException(404,"Устройство не найдено")
 await revoke(row,request,db);return out(row)

@router.delete("/devices/{device_id}",status_code=204)
async def delete_device(device_id:int,request:Request,db:AsyncSession=Depends(get_db)):
 row=await db.get(AccessDevice,device_id)
 if not row or row.deleted_at:raise HTTPException(404,"Устройство не найдено")
 await revoke(row,request,db);row.deleted_at=datetime.now(timezone.utc);audit(db,row.id,"deleted",ip(request));await db.commit();return Response(status_code=204)

@router.post("/devices/{device_id}/certificate",status_code=201)
async def replace_certificate(device_id:int,request:Request,db:AsyncSession=Depends(get_db)):
 row=await db.get(AccessDevice,device_id)
 if not row or row.deleted_at:raise HTTPException(404,"Устройство не найдено")
 await revoke(row,request,db)
 return await issue(DeviceCreate(device_name=row.device_name,owner=row.owner,comment=row.comment),request,db)

@router.get("/devices/{device_id}/certificate/download")
async def download_certificate(device_id:int,request:Request,token:str=Header(alias="X-Download-Token",min_length=32,max_length=256),db:AsyncSession=Depends(get_db)):
 row=await db.get(AccessDevice,device_id)
 if not row or row.deleted_at:raise HTTPException(404,"Устройство не найдено")
 if not row.package_available:raise HTTPException(410,"Установочный файл больше недоступен")
 try:result=await helper_call("download",serial_number=row.serial_number,download_token=token)
 except MtlsHelperError as exc:raise HTTPException(410,str(exc)) from exc
 row.package_available=False;row.package_expires_at=None;audit(db,row.id,"downloaded",ip(request));await db.commit()
 return Response(base64.b64decode(result["package"]),media_type="application/x-pkcs12",headers={"Content-Disposition":f'attachment; filename="{row.common_name}.p12"',"Cache-Control":"no-store"})

@router.post("/existing-devices",status_code=201)
async def register_existing(request:Request,device_name:str=Query(min_length=2,max_length=255),owner:str|None=None,comment:str|None=None,
 certificate:UploadFile=File(...),db:AsyncSession=Depends(get_db)):
 content=await certificate.read(1024*1024+1)
 if len(content)>1024*1024:raise HTTPException(413,"Публичный сертификат слишком большой")
 try:result=await helper_call("register",certificate=base64.b64encode(content).decode())
 except MtlsHelperError as exc:raise HTTPException(422,str(exc)) from exc
 if await db.scalar(select(AccessDevice.id).where(or_(AccessDevice.common_name==result["common_name"],AccessDevice.serial_number==result["serial_number"],AccessDevice.fingerprint==result["fingerprint"]))):
  raise HTTPException(409,"Сертификат уже зарегистрирован")
 row=AccessDevice(device_name=device_name,owner=owner,comment=comment,common_name=result["common_name"],serial_number=result["serial_number"],
  fingerprint=result["fingerprint"],created_at=parsed(result["created_at"]),expires_at=parsed(result["expires_at"]),status="active",package_available=False)
 db.add(row);await db.flush();audit(db,row.id,"registered",ip(request),{"common_name":row.common_name});await db.commit();await db.refresh(row);return out(row)

@router.get("/audit")
async def audit_events(db:AsyncSession=Depends(get_db)):
 rows=(await db.scalars(select(AccessAuditEvent).order_by(AccessAuditEvent.created_at.desc()).limit(500))).all()
 return [{"id":row.id,"device_id":row.device_id,"action":row.action,"actor":row.actor,"ip_address":row.ip_address,"details":row.details,"created_at":row.created_at} for row in rows]
