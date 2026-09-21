import asyncio, logging
from contextlib import asynccontextmanager
from contextlib import suppress
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.config import settings
from app.database import engine
from app.routers import analytics,ftp,horeca_reports,imports,integrations,meta,product_reports,sales,settings as settings_router
from app.services.ftp_autoload import scheduler
from app.services.scenarios import scheduler as scenario_scheduler
logging.basicConfig(level=settings.log_level,format="%(asctime)s %(levelname)s %(name)s %(message)s")
log=logging.getLogger(__name__)
@asynccontextmanager
async def lifespan(app):
 task=asyncio.create_task(scheduler());scenario_task=asyncio.create_task(scenario_scheduler())
 log.info("Запуск приложения")
 try:
  async with engine.connect() as conn:await conn.execute(text("SELECT 1"));log.info("Подключение к PostgreSQL установлено")
 except Exception:log.exception("PostgreSQL недоступен при запуске")
 yield
 task.cancel()
 scenario_task.cancel()
 with suppress(asyncio.CancelledError):await task
 with suppress(asyncio.CancelledError):await scenario_task
 await engine.dispose()
app=FastAPI(title=settings.app_name,root_path=settings.base_path,docs_url="/api/docs",openapi_url="/api/openapi.json",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=settings.origins,allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
for router in (sales.router,imports.router,analytics.router,meta.router,ftp.router,integrations.router,product_reports.router,horeca_reports.router,settings_router.router):app.include_router(router,prefix="/api")
@app.get("/api/health")
async def health():return {"status":"ok"}
