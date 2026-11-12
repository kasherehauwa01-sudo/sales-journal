import asyncio, ftplib, logging, re, uuid
from dataclasses import dataclass
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

log = logging.getLogger(__name__)
run_lock = asyncio.Lock()


def _connect(config: FtpConfig):
    client = ftplib.FTP_TLS() if config.protocol == "FTPS" else ftplib.FTP()
    try:
        client.connect(config.host, config.port, timeout=30)
        client.login(config.username, config.password)
        if isinstance(client, ftplib.FTP_TLS):
            client.prot_p()
        client.cwd(config.directory)
        return client
    except BaseException:
        client.close()
        raise


async def _with_retries(config, operation):
    last = None
    for attempt in range(config.retries):
        try:
            from app.services.async_resources import run_blocking

            return await run_blocking(operation)
        except Exception as exc:
            last = exc
            if attempt + 1 < config.retries:
                await asyncio.sleep(config.retry_delay)
    raise last


async def test_connection(config: FtpConfig):
    def action():
        client = _connect(config)
        try:
            return len(client.nlst())
        finally:
            try:
                client.quit()
            except Exception:
                client.close()

    return await _with_retries(config, action)


@dataclass(frozen=True, repr=False)
class FtpSettings:
    protocol: str
    host: str
    port: int
    username: str
    password: str
    directory: str
    retries: int
    retry_delay: int


async def load_config():
    # Return scalars, not an ORM object/transaction spanning network operations.
    async with SessionLocal() as db:
        config = await db.get(FtpConfig, 1)
        if not config or not config.enabled:
            return None
        return FtpSettings(
            **{name: getattr(config, name) for name in FtpSettings.__dataclass_fields__}
        )


async def _run_autoload_cycle(cycle_id=None, day=None, marker_template=None):
    if run_lock.locked():
        return
    async with run_lock:
        config = await load_config()
        if config is None:
            return
        try:

            def list_files():
                client = _connect(config)
                try:
                    return importable_ftp_files(client.nlst()), False
                finally:
                    try:
                        client.quit()
                    except Exception:
                        client.close()

            filenames, marker_seen = await _with_retries(config, list_files)
        except Exception as exc:
            async with SessionLocal() as db:
                db.add(
                    AutoImportLog(
                        status="failed",
                        message="ftp_listing_failed:" + type(exc).__name__,
                        finished_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()
            if cycle_id:
                from app.services.import_control_worker import safe_hook, finish_cycle

                await safe_hook(finish_cycle, cycle_id, False, "ftp_listing_failed")
            return
        if not filenames:
            async with SessionLocal() as db:
                db.add(
                    AutoImportLog(
                        status="empty",
                        message="Файлы XLS/XLSX/HTML на FTP не найдены",
                        finished_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()
        for filename in filenames:
            await _process_file(config, filename, cycle_id)
        if cycle_id:
            from app.services.import_control_worker import safe_hook, finish_cycle

            await safe_hook(finish_cycle, cycle_id, marker_seen)


async def _process_file(config: FtpSettings, filename: str, cycle_id=None):
    safe = re.sub(r"[^\w. -]", "_", Path(filename).name)
    local = import_temp_dir() / f"ftp_{uuid.uuid4().hex}_{safe}"
    async with SessionLocal() as db:
        entry = AutoImportLog(filename=filename, status="downloading")
        db.add(entry)
        await db.commit()
        entry_id = entry.id
    batch_id = None
    status, message = "failed", "ftp_operation_failed"
    try:

        def download():
            client = _connect(config)
            try:
                with local.open("wb") as target:
                    client.retrbinary(f"RETR {filename}", target.write)
            finally:
                try:
                    client.quit()
                except Exception:
                    client.close()

        await _with_retries(config, download)
        async with SessionLocal() as db:
            batch = ImportBatch(
                filename=safe,
                stored_path=str(local),
                file_size=local.stat().st_size,
                status="queued",
            )
            db.add(batch)
            await db.flush()
            batch_id = batch.id
            entry = await db.get(AutoImportLog, entry_id)
            entry.import_id = batch_id
            entry.status = "processing"
            await db.commit()
        from app.services.import_control_worker import safe_hook, receipt_before

        await safe_hook(receipt_before, batch_id, filename, cycle_id)
        await process_import(batch_id)
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    select(
                        ImportBatch.status,
                        ImportBatch.error_rows,
                        ImportBatch.added_rows,
                        ImportBatch.duplicate_rows,
                    ).where(ImportBatch.id == batch_id)
                )
            ).one()
        success = row.status == "completed" and row.error_rows == 0

        def finish_remote():
            client = _connect(config)
            try:
                if success:
                    client.delete(filename)
                elif not filename.upper().startswith("EROOR_"):
                    client.rename(filename, f"EROOR_{filename}")
            finally:
                try:
                    client.quit()
                except Exception:
                    client.close()

        await _with_retries(config, finish_remote)
        status = "completed" if success else "failed"
        message = (
            f"Загружено строк: {row.added_rows}, дублей: {row.duplicate_rows}"
            if success
            else f"Импорт завершён с ошибками: {row.error_rows}. Файл переименован."
        )
    except asyncio.CancelledError:
        message = "Обработка прервана"
        raise
    except Exception as exc:
        message = "ftp_operation_failed:" + type(exc).__name__
        if batch_id is None and cycle_id:
            async with SessionLocal() as db:
                failed = ImportBatch(
                    filename=safe,
                    stored_path=None,
                    file_size=local.stat().st_size if local.exists() else 0,
                    status="failed",
                    error_text="ftp_download_failed",
                    finished_at=datetime.now(timezone.utc),
                )
                db.add(failed)
                await db.flush()
                batch_id = failed.id
                entry = await db.get(AutoImportLog, entry_id)
                entry.import_id = batch_id
                await db.commit()
            from app.services.import_control_worker import (
                record_download_error,
                safe_hook,
            )

            await safe_hook(record_download_error, batch_id, filename, cycle_id)
        try:

            def mark_error():
                client = _connect(config)
                try:
                    if not filename.upper().startswith("EROOR_"):
                        client.rename(filename, f"EROOR_{filename}")
                finally:
                    try:
                        client.quit()
                    except Exception:
                        client.close()

            await _with_retries(config, mark_error)
        except Exception as rename_exc:
            message += ";ftp_rename_failed:" + type(rename_exc).__name__
    finally:
        local.unlink(missing_ok=True)
        async with SessionLocal() as db:
            entry = await db.get(AutoImportLog, entry_id)
            entry.status = status
            entry.message = message
            entry.finished_at = datetime.now(timezone.utc)
            await db.commit()


async def run_autoload(sales_day=None):
    # Session-level lock prevents cross-process overlapping FTP cycles.
    from app.services.import_control_worker import (
        owned_lock,
        safe_hook,
        begin_cycle,
        finish_cycle,
    )

    async with owned_lock(engine, FTP_LOCK) as acquired:
        if not acquired:
            return
        async with SessionLocal() as db:
            ftp_config = await db.get(FtpConfig, 1)
            if not ftp_config or not ftp_config.enabled:
                return
            if sales_day:
                from app.models.import_control import ImportCycle
                from app.services.import_deadline import deadline

                done = await db.scalar(
                    select(ImportCycle.id)
                    .where(
                        ImportCycle.sales_date == sales_day,
                        ImportCycle.started_at >= deadline(sales_day),
                        ImportCycle.status.in_(("finished", "failed")),
                    )
                    .limit(1)
                )
                if done:
                    return
        cycle = await safe_hook(begin_cycle, sales_day)
        try:
            await _run_autoload_cycle(*(cycle or (None, None, None)))
        except BaseException:
            if cycle:
                await safe_hook(finish_cycle, cycle[0], False, "cycle_interrupted")
            raise


async def scheduler():
    zone = ZoneInfo(settings.autoload_timezone)
    while True:
        now = datetime.now(zone)
        target = datetime.combine(now.date(), time(23, 55), zone)
        if target <= now:
            target += timedelta(days=1)
        while datetime.now(zone) < target:
            await asyncio.sleep(
                min(60, max(0, (target - datetime.now(zone)).total_seconds()))
            )
        await run_autoload()
