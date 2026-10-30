"""Print a browser-authorized snapshot; never query data or accept a URL."""
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from app.services.product_analytics_runtime import heavy_operation
from pydantic import BaseModel, Field, ValidationError
from app.services.report_pdf import MAX_HTML_BYTES, render_pdf

class PdfRoute(APIRoute):
    async def handle(self, scope, receive, send):
        async with heavy_operation('pdf_request'):
            return await super().handle(scope, receive, send)

router = APIRouter(route_class=PdfRoute, prefix='/reports', tags=['Отчеты'])
REPORTS = {
    'rfm-analysis':'RFM-анализ клиентов', 'product-sales':'Продажи по товарам',
    'product-analytics':'Товарная аналитика', 'category-analytics':'Аналитика категорий',
    'seasonality':'Сезонность', 'promotion-candidates':'Кандидаты в акцию',
    'sales-dynamics':'Динамика продаж', 'store-analytics':'Аналитика по магазинам',
}

class Snapshot(BaseModel):
    report: str
    period: str = Field(max_length=150)
    filename: str = Field(pattern=r'^[a-z0-9_-]+\.pdf$', max_length=180)
    html: str = Field(min_length=1)

@router.post('/pdf')
async def export_pdf(request: Request):
    # Same protected ingress as ordinary reports; no service token bypass, no URL
    # fetch and no access to another report/user's data. Stream size is bounded
    # before JSON parsing (also for requests without Content-Length).
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_HTML_BYTES:
            raise HTTPException(413, 'Печатная версия слишком велика для доступных ресурсов. PDF не был сокращён.')
    try:
        payload = Snapshot.model_validate(json.loads(body))
    except (ValidationError, ValueError, UnicodeDecodeError):
        raise HTTPException(422, 'Некорректные параметры PDF.') from None
    if payload.report not in REPORTS:
        raise HTTPException(422, 'Неизвестный отчёт.')
    pdf = await render_pdf(payload.html, REPORTS[payload.report], payload.period)
    return Response(pdf, media_type='application/pdf', headers={
        'Content-Disposition':f'attachment; filename="{payload.filename}"',
        'Cache-Control':'no-store, private', 'X-Content-Type-Options':'nosniff',
    })
