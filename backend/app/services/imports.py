import json, logging, time
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.database import SessionLocal
from app.importer.parser import include_document, include_for_filename, normalize_sale, read_sales
from app.models import ImportBatch, ImportError, Sale, SaleItem
log=logging.getLogger(__name__)
async def process_import(import_id:int):
 started=time.monotonic()
 async with SessionLocal() as db:
  batch=await db.get(ImportBatch,import_id); batch.status="processing";batch.started_at=datetime.now(timezone.utc);batch.log_text=f"[{batch.started_at.isoformat()}] Начата обработка файла {batch.filename}\n";await db.commit()
  try:
   sheet,rows=read_sales(Path(batch.stored_path));batch.log_text+=f"Найден лист «{sheet}». Строки обрабатываются последовательно.\n";await db.commit()
   period_start=None;period_end=None
   for number,raw in rows:
    batch.total_rows+=1
    try:
     if not include_for_filename(batch.filename,raw) or not include_document(raw):batch.skipped_rows+=1;batch.processed_rows+=1;await db.commit();continue
     data=normalize_sale(raw);sale_date=data["sale_date"]
     period_start=sale_date if period_start is None or sale_date<period_start else period_start
     period_end=sale_date if period_end is None or sale_date>period_end else period_end
     items=data.pop("items");legacy_fingerprint=data.pop("legacy_fingerprint",None)
     fingerprints=[data["fingerprint"],legacy_fingerprint] if legacy_fingerprint else [data["fingerprint"]]
     exists=await db.scalar(select(Sale.id).where(Sale.fingerprint.in_(fingerprints)))
     if exists:batch.duplicate_rows+=1;batch.processed_rows+=1;await db.commit();continue
     sale=Sale(**data,import_id=batch.id);db.add(sale);await db.flush()
     db.add_all([SaleItem(sale_id=sale.id,**item) for item in items]);batch.added_rows+=1;batch.processed_rows+=1
     await db.commit()
     # AsyncSession использует слабую identity map, но явное удаление сильных
     # локальных ссылок позволяет освободить ORM-объекты сразу после транзакции.
     db.expunge(sale);del sale,items,data
    except Exception as exc:
     await db.rollback();batch=await db.get(ImportBatch,import_id);batch.total_rows+=1;batch.error_rows+=1;batch.processed_rows+=1
     message=str(exc)[:2000];batch.log_text=(batch.log_text or "")+f"Строка {number}: {message}\n";db.add(ImportError(import_id=batch.id,row_number=number,message=message,raw_data=json.dumps(raw,ensure_ascii=False,default=str)[:10000]));await db.commit()
   batch=await db.get(ImportBatch,import_id);batch.period_start=period_start;batch.period_end=period_end;batch.status="completed";batch.finished_at=datetime.now(timezone.utc);batch.duration_ms=int((time.monotonic()-started)*1000);batch.log_text=(batch.log_text or "")+f"Завершено: обработано {batch.processed_rows}, добавлено {batch.added_rows}, дублей {batch.duplicate_rows}, пропущено {batch.skipped_rows}, ошибок {batch.error_rows}.\n";await db.commit()
   log.info("Импорт %s завершён: добавлено %s, дублей %s, ошибок %s",import_id,batch.added_rows,batch.duplicate_rows,batch.error_rows)
  except Exception as exc:
   await db.rollback();batch=await db.get(ImportBatch,import_id);batch.status="failed";batch.error_text=str(exc)[:10000];batch.finished_at=datetime.now(timezone.utc);batch.duration_ms=int((time.monotonic()-started)*1000);batch.log_text=(batch.log_text or "")+f"Критическая ошибка: {batch.error_text}\n";await db.commit();log.exception("Ошибка импорта %s",import_id)
