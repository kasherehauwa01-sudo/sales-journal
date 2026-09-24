import re, uuid
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.config import settings
from app.database import get_db
from app.models import ImportBatch
from app.schemas import ImportOut
from app.services.imports import process_import
router=APIRouter(prefix="/imports",tags=["Импорт"])
@router.post("",response_model=list[ImportOut],status_code=202)
async def upload(background:BackgroundTasks,files:list[UploadFile]=File(...),db:AsyncSession=Depends(get_db)):
 settings.upload_dir.mkdir(parents=True,exist_ok=True);result=[]
 for f in files:
  ext=Path(f.filename or "").suffix.lower()
  if ext not in {".xls",".xlsx",".html",".htm"}:raise HTTPException(415,"Поддерживаются только XLS/XLSX/HTML")
  safe=re.sub(r"[^\w. -]","_",Path(f.filename or "sales").name);path=settings.upload_dir/f"{uuid.uuid4().hex}_{safe}";content=await f.read();path.write_bytes(content)
  batch=ImportBatch(filename=safe,stored_path=str(path),file_size=len(content),status="queued");db.add(batch);await db.flush();await db.refresh(batch);result.append(batch);background.add_task(process_import,batch.id)
 await db.commit();return result
@router.get("",response_model=list[ImportOut])
async def history(db:AsyncSession=Depends(get_db)):return (await db.scalars(select(ImportBatch).options(selectinload(ImportBatch.errors)).order_by(ImportBatch.id.desc()).limit(200))).unique().all()
@router.get("/{import_id}",response_model=ImportOut)
async def detail(import_id:int,db:AsyncSession=Depends(get_db)):
 obj=(await db.scalars(select(ImportBatch).options(selectinload(ImportBatch.errors)).where(ImportBatch.id==import_id))).first()
 if not obj:raise HTTPException(404,"Импорт не найден")
 return obj
