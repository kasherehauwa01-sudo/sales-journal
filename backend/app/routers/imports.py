import re, uuid
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models import ImportBatch
from app.schemas import ImportOut
from app.services.import_files import import_temp_dir
from app.services.imports import process_import
from app.services.settings_auth import require_settings_admin
router=APIRouter(prefix="/imports",tags=["Импорт"],dependencies=[Depends(require_settings_admin)])
UPLOAD_CHUNK_SIZE=1024*1024

async def _save_upload(source:UploadFile,path:Path)->int:
 """Пишет upload на диск частями, не создавая полную bytes-копию файла."""
 size=0
 with path.open("wb") as target:
  while chunk:=await source.read(UPLOAD_CHUNK_SIZE):
   target.write(chunk);size+=len(chunk)
 return size

@router.post("",response_model=list[ImportOut],status_code=202)
async def upload(background:BackgroundTasks,files:list[UploadFile]=File(...),db:AsyncSession=Depends(get_db)):
 temp_dir=import_temp_dir();result=[];saved_paths=[]
 try:
  for f in files:
   ext=Path(f.filename or "").suffix.lower()
   if ext not in {".xls",".xlsx",".html",".htm"}:raise HTTPException(415,"Поддерживаются только XLS/XLSX/HTML")
   safe=re.sub(r"[^\w. -]","_",Path(f.filename or "sales").name);path=temp_dir/f"{uuid.uuid4().hex}_{safe}"
   file_size=await _save_upload(f,path);saved_paths.append(path)
   batch=ImportBatch(filename=safe,stored_path=str(path),file_size=file_size,status="queued");db.add(batch);await db.flush();await db.refresh(batch);result.append(batch);background.add_task(process_import,batch.id)
  await db.commit();return result
 except Exception:
  await db.rollback()
  for path in saved_paths:path.unlink(missing_ok=True)
  # _save_upload мог создать файл до исключения и до добавления в saved_paths.
  if "path" in locals():path.unlink(missing_ok=True)
  raise
@router.get("",response_model=list[ImportOut])
async def history(db:AsyncSession=Depends(get_db)):return (await db.scalars(select(ImportBatch).options(selectinload(ImportBatch.errors)).order_by(ImportBatch.id.desc()).limit(200))).unique().all()
@router.get("/{import_id}",response_model=ImportOut)
async def detail(import_id:int,db:AsyncSession=Depends(get_db)):
 obj=(await db.scalars(select(ImportBatch).options(selectinload(ImportBatch.errors)).where(ImportBatch.id==import_id))).first()
 if not obj:raise HTTPException(404,"Импорт не найден")
 return obj
