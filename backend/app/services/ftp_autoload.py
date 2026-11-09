import asyncio, ftplib, logging, re, uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select, text
from app.config import settings
from app.database import SessionLocal, engine
from app.models import AutoImportLog, FtpConfig, ImportBatch
from app.services.ftp_file_utils import importable_ftp_files
from app.services.import_files import import_temp_dir
from app.services.imports import process_import
from app.services.import_control import FTP_LOCK

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

async def _run_autoload_cycle(cycle_id=None,day=None,marker_template=None):
 if run_lock.locked():return
 async with run_lock:
  async with SessionLocal() as db:
   config=await db.get(FtpConfig,1)
   if not config or not config.enabled:return
   try:
    def list_files():
     client=_connect(config)
     try:
      names=client.nlst()
      marker=marker_template.replace('{date}',day.strftime('%d.%m.%Y')) if marker_template and day else None
      marker_seen=bool(marker and marker in [Path(str(value).replace('\\','/')).name for value in names])
      return importable_ftp_files(names),marker_seen
     finally:
      try:client.quit()
      except Exception:client.close()
    filenames,marker_seen=await _with_retries(config,list_files)
   except Exception as exc:
    db.add(AutoImportLog(status="failed",message=f"Не удалось получить список FTP: {exc}",finished_at=datetime.now(timezone.utc)));await db.commit()
    if cycle_id:
     from app.services.import_control_worker import safe_hook,finish_cycle
     await safe_hook(finish_cycle,cycle_id,False,'ftp_listing_failed')
    return
   if not filenames:db.add(AutoImportLog(status="empty",message="Файлы XLS/XLSX/HTML на FTP не найдены",finished_at=datetime.now(timezone.utc)));await db.commit()
   for filename in filenames:await _process_file(config,filename,cycle_id)
   if cycle_id:
    from app.services.import_control_worker import safe_hook,finish_cycle
    await safe_hook(finish_cycle,cycle_id,marker_seen)

async def _process_file(config:FtpConfig,filename:str,cycle_id=None):
 safe=re.sub(r"[^\w. -]","_",Path(filename).name);local=import_temp_dir()/f"ftp_{uuid.uuid4().hex}_{safe}"
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
   from app.services.import_control_worker import safe_hook,receipt_before
   await safe_hook(receipt_before,batch.id,filename,cycle_id)
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
  except asyncio.CancelledError:
   entry.status="failed";entry.message="Обработка прервана"
   raise
  except Exception as exc:
   entry.status="failed";entry.message=str(exc)[:4000]
   if not entry.import_id and cycle_id:
    # Record a failed receipt without retaining partial bytes or inventing sales.
    failed=ImportBatch(filename=safe,stored_path=None,file_size=local.stat().st_size if local.exists() else 0,status="failed",error_text="ftp_download_failed",finished_at=datetime.now(timezone.utc))
    db.add(failed);await db.flush();entry.import_id=failed.id;await db.commit()
    from app.services.import_control_worker import record_download_error,safe_hook
    await safe_hook(record_download_error,failed.id,filename,cycle_id)
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
   # process_import удаляет полностью загруженный файл; этот finally также
   # очищает частичный файл при ошибке скачивания до создания ImportBatch.
   local.unlink(missing_ok=True);entry.finished_at=datetime.now(timezone.utc);await db.commit()

async def run_autoload():
 # Session-level lock prevents cross-process overlapping FTP cycles.
 from app.services.import_control_worker import owned_lock,safe_hook,begin_cycle,finish_cycle
 async with owned_lock(engine,FTP_LOCK) as acquired:
  if not acquired:return
  async with SessionLocal() as db:
   ftp_config=await db.get(FtpConfig,1)
   if not ftp_config or not ftp_config.enabled:return
  cycle=await safe_hook(begin_cycle)
  try:
   await _run_autoload_cycle(*(cycle or (None,None,None)))
  except BaseException:
   if cycle:await safe_hook(finish_cycle,cycle[0],False,'cycle_interrupted')
   raise

async def scheduler():
 zone=ZoneInfo(settings.autoload_timezone)
 while True:
  now=datetime.now(zone);target=datetime.combine(now.date(),time(23,55),zone)
  if target<=now:target+=timedelta(days=1)
  while datetime.now(zone)<target:
   await asyncio.sleep(min(60,max(0,(target-datetime.now(zone)).total_seconds())))
   from app.services.import_control_worker import safe_hook,maintenance
   await safe_hook(maintenance)
  await run_autoload()
