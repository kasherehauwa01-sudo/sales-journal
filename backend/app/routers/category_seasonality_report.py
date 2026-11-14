"""Read-only, explicitly requested subcategory seasonality. No persistence/jobs."""

from datetime import datetime
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, ConfigDict
from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from app.models import CatalogProduct
from app.config import settings
from app.services.report_execution import get_report_db, ReportRoute
from app.services import category_seasonality as service

router = APIRouter(
    route_class=ReportRoute, prefix="/reports/category-seasonality", tags=["Отчеты"]
)


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    years: list[int] = Field(min_length=1, max_length=200)
    stores: list[str] = Field(default_factory=list, max_length=200)
    categories: list[str] = Field(default_factory=list, max_length=5000)
    brands: list[str] = Field(default_factory=list, max_length=1000)
    manufacturers: list[str] = Field(default_factory=list, max_length=1000)
    manager: list[str] = Field(default_factory=list, max_length=200)
    buyer_type: list[str] = Field(default_factory=list, max_length=200)


@router.get("/options")
async def options(db=Depends(get_report_db)):
    # Metadata only; no item scan or calculation. Years use sale_date index probes.
    years = list(
        (
            await db.scalars(
                text(
                    "SELECT y FROM generate_series((SELECT EXTRACT(YEAR FROM min(sale_date))::int FROM sales), :last_year) y WHERE EXISTS (SELECT 1 FROM sales WHERE sale_date >= make_date(y,1,1) AND sale_date < make_date(y+1,1,1)) ORDER BY y DESC"
                ),
                {
                    "last_year": datetime.now(ZoneInfo(settings.autoload_timezone)).year
                    - 1
                },
            )
        ).all()
    )
    _, nodes = await service.directory(db)
    result = {"years": years, "nodes": nodes}
    for field in ("brand", "manufacturer"):
        col = getattr(CatalogProduct, field)
        result[field] = list(
            (
                await db.scalars(
                    select(col)
                    .where(col.is_not(None), col != "")
                    .distinct()
                    .order_by(col)
                )
            ).all()
        )
    return result


@router.post("")
async def overview(data: Request, db=Depends(get_report_db)):
    # Resource settings started the transaction, but no data was read yet.
    # Set isolation before catalog/client/coverage reads to share one snapshot.
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    return await service.build(
        db, data, datetime.now(ZoneInfo(settings.autoload_timezone)).date()
    )
