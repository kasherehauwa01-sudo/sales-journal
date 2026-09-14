import json, logging, time
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.database import SessionLocal
from app.importer.parser import normalize_sale, read_sales
from app.models import ImportBatch, ImportError, Sale, SaleItem
log=logging.getLogger(__name__)
async def process_import(import_id:int):
 started=time.monotonic()
 async with SessionLocal() as db:
  batch=await db.get(ImportBatch,import_id); batch.status="processing";batch.started_at=datetime.now(timezone.utc);await db.commit()
  try:
   _, rows=read_sales(Path(batch.stored_path)); batch.total_rows=len(rows); dates=[]
   for number,raw in rows:
    try:
     data=normalize_sale(raw); dates.append(data["sale_date"]); items=data.pop("items")
     exists=await db.scalar(select(Sale.id).where(Sale.fingerprint==data["fingerprint"]))
     if exists:batch.duplicate_rows+=1;batch.processed_rows+=1;continue
     sale=Sale(**data,import_id=batch.id);db.add(sale);await db.flush()
     db.add_all([SaleItem(sale_id=sale.id,**item) for item in items]);batch.added_rows+=1;batch.processed_rows+=1
     await db.commit()
    except Exception as exc:
     await db.rollback();batch=await db.get(ImportBatch,import_id);batch.error_rows+=1;batch.processed_rows+=1
     db.add(ImportError(import_id=batch.id,row_number=number,message=str(exc)[:2000],raw_data=json.dumps(raw,ensure_ascii=False,default=str)[:10000]));await db.commit()
   batch=await db.get(ImportBatch,import_id);batch.period_start=min(dates) if dates else None;batch.period_end=max(dates) if dates else None;batch.status="completed";batch.finished_at=datetime.now(timezone.utc);batch.duration_ms=int((time.monotonic()-started)*1000);await db.commit()
   log.info("Импорт %s завершён: добавлено %s, дублей %s, ошибок %s",import_id,batch.added_rows,batch.duplicate_rows,batch.error_rows)
  except Exception as exc:
   await db.rollback();batch=await db.get(ImportBatch,import_id);batch.status="failed";batch.error_text=str(exc)[:10000];batch.finished_at=datetime.now(timezone.utc);batch.duration_ms=int((time.monotonic()-started)*1000);await db.commit();log.exception("Ошибка импорта %s",import_id)
