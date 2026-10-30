"""Per-request resource limits for read-only sales reports, never server defaults."""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, HTTPException
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError

from app.database import get_db
from app.services.product_analytics_runtime import heavy_operation

log = logging.getLogger(__name__)

# work_mem is per execution node, NOT a bound on total PostgreSQL memory.
# Serial plans avoid parallel hash DSM allocations in Docker /dev/shm.
REPORT_SETTINGS = (
    "SET LOCAL max_parallel_workers_per_gather = 0",
    "SET LOCAL work_mem = '4MB'",
    "SET LOCAL hash_mem_multiplier = 1",
    "SET LOCAL statement_timeout = '60s'",
    "SET LOCAL lock_timeout = '2s'",
)


class ReportRoute(APIRoute):
    """Admit expensive report requests before reading their potentially huge body."""
    async def handle(self, scope, receive, send):
        guarded = self.name in {
            "search_products", "summary", "products", "dynamics", "departments",
            "managers", "details", "export", "overview", "checks", "clients",
            "discounts", "promotions",
        }
        if not guarded:
            return await super().handle(scope, receive, send)
        async with heavy_operation("sales_report_response"):
            return await super().handle(scope, receive, send)


@asynccontextmanager
async def report_transaction(db: AsyncSession):
    # Share admission with existing product analytics across API worker processes.
    # Admission precedes connection checkout. Hold the slot through rollback so
    # another report cannot overlap an unfinished failed/cancelled transaction.
    async with heavy_operation("sales_report"):
        is_postgres = getattr(getattr(getattr(db, "bind", None), "dialect", None), "name", None) == "postgresql"
        try:
            if is_postgres:
                for statement in REPORT_SETTINGS:
                    await db.execute(text(statement))
            yield db
        except DBAPIError as exc:
            state = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
            if state in {"53100", "53200", "53300", "57014"}:
                # No SQL text, parameter values or full exception string in logs.
                log.warning("Sales report resource failure sqlstate=%s driver=%s", state, type(exc.orig).__name__)
                raise HTTPException(503, "Недостаточно ресурсов для формирования отчёта или превышено время ожидания. Повторите запрос позже.", headers={"Retry-After": "30"}) from None
            raise
        finally:
            if is_postgres:
                await db.rollback()


async def get_report_db(db: AsyncSession = Depends(get_db)):
    async with report_transaction(db) as session:
        yield session

class FullReportRoute(APIRoute):
    """Guard full export datasets only; ordinary page browsing is unchanged."""
    async def handle(self, scope, receive, send):
        from urllib.parse import parse_qs
        from app.services.product_analytics_runtime import ensure_headroom
        values = parse_qs(scope.get('query_string', b'').decode('ascii', errors='ignore')).get('full', [])
        if any(value.lower() in {'true','1','yes','on'} for value in values):
            async with heavy_operation('full_report_pdf_data'):
                ensure_headroom()
                return await super().handle(scope, receive, send)
        return await super().handle(scope, receive, send)
