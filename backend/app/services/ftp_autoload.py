import asyncio, ftplib, logging, re, uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.config import settings
from app.database import SessionLocal
from app.models import AutoImportLog, FtpConfig, ImportBatch
from app.services.ftp_file_utils import importable_ftp_files
from app.services.imports import process_import

log=logging.getLogger(__name__);run_lock=asyncio.Lock()

def _connect(config:FtpConfig):
 client=(ftplib.FTP_TLS() if config.protocol=="FTPS" else ftplib.FTP())
 client.connect(config.host,config.port,timeout=30);client.login(config.username,config.password)
 if isinstance(client,ftplib.FTP_TLS):client.prot_p()
 client.cwd(config.directory);return client

async def _with_retries(config,operation):
 last=None
 for attempt in range(config.retries):
  try:return await asyncio.to_thread(operation)
  except Exception as exc:
   last=exc
   if attempt+1<config.retries:await asyncio.sleep(config.retry_delay)
 raise last

async def test_connection(config:FtpConfig):
 def action():
  client=_connect(config)
  try:return len(client.nlst())
  finally:
   try:client.quit()
   except Exception:client.close()
 return await _with_retries(config,action)

async def run_autoload():
 if run_lock.locked():return
 async with run_lock:
  async with SessionLocal() as db:
   config=await db.get(FtpConfig,1)
   if not config or not config.enabled:return
   try:
    def list_files():
     client=_connect(config)
     try:return importable_ftp_files(client.nlst())
     finally:
      try:client.quit()
      except Exception:client.close()
    filenames=await _with_retries(config,list_files)
   except Exception as exc:
    db.add(AutoImportLog(status="failed",message=f"Не удалось получить список FTP: {exc}",finished_at=datetime.now(timezone.utc)));await db.commit();return
   if not filenames:db.add(AutoImportLog(status="empty",message="Файлы XLS/XLSX/HTML на FTP не найдены",finished_at=datetime.now(timezone.utc)));await db.commit();return
   for filename in filenames:await _process_file(config,filename)

async def _process_file(config:FtpConfig,filename:str):
 settings.upload_dir.mkdir(parents=True,exist_ok=True);safe=re.sub(r"[^\w. -]","_",Path(filename).name);local=settings.upload_dir/f"ftp_{uuid.uuid4().hex}_{safe}"
 async with SessionLocal() as db:
  entry=AutoImportLog(filename=filename,status="downloading");db.add(entry);await db.commit();await db.refresh(entry)
  try:
   def download():
    client=_connect(config)
    try:
     with local.open("wb") as target:client.retrbinary(f"RETR {filename}",target.write)
    finally:
     try:client.quit()
     except Exception:client.close()
   await _with_retries(config,download)
   batch=ImportBatch(filename=safe,stored_path=str(local),file_size=local.stat().st_size,status="queued");db.add(batch);await db.commit();await db.refresh(batch);entry.import_id=batch.id;entry.status="processing";await db.commit()
   await process_import(batch.id);await db.refresh(batch)
   success=batch.status=="completed" and batch.error_rows==0
   def finish_remote():
    client=_connect(config)
    try:
     if success:client.delete(filename)
     elif not filename.upper().startswith("EROOR_"):client.rename(filename,f"EROOR_{filename}")
    finally:
     try:client.quit()
     except Exception:client.close()
   await _with_retries(config,finish_remote)
   entry.status="completed" if success else "failed";entry.message=(f"Загружено строк: {batch.added_rows}, дублей: {batch.duplicate_rows}" if success else f"Импорт завершён с ошибками: {batch.error_rows}. Файл переименован.")
  except Exception as exc:
   entry.status="failed";entry.message=str(exc)[:4000]
   try:
    def mark_error():
     client=_connect(config)
     try:
      if not filename.upper().startswith("EROOR_"):client.rename(filename,f"EROOR_{filename}")
     finally:
      try:client.quit()
      except Exception:client.close()
    await _with_retries(config,mark_error)
   except Exception as rename_exc:entry.message+=f"; не удалось переименовать: {rename_exc}"
  finally:
   entry.finished_at=datetime.now(timezone.utc);await db.commit()

async def scheduler():
 zone=ZoneInfo(settings.autoload_timezone)
 while True:
  now=datetime.now(zone);target=datetime.combine(now.date(),time(23,55),zone)
  if target<=now:target+=timedelta(days=1)
  await asyncio.sleep((target-now).total_seconds());await run_autoload()
