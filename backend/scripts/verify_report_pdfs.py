"""Real-browser smoke tests using only synthetic frontend fixtures and an ASGI
PDF renderer (no app lifespan, database, production calls or tasks).
Start Vite locally first; see docs/analytics_pdf_export.md.
"""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI
from playwright.async_api import async_playwright
from app.routers.report_pdf import router

CASES = [
 ('rfm','rfm-analysis','RFM-анализ клиентов','Тестовый клиент 499'),
 ('product-sales','product-sales','Продажи по товарам','Тестовый товар 549'),
 ('product-analytics','product-analytics','Товарная аналитика','Тестовый товар 549'),
 ('category-analytics','category-analytics','Аналитика категорий','Категория1'),
 ('seasonality','seasonality','Сезонность','Сезон сейчас'),
 ('promotion-candidates','promotion-candidates','Кандидаты в акцию','Тестовый кандидат 499'),
 ('sales-dynamics','sales-dynamics','Динамика продаж','Выручка'),
 ('store-analytics','store-analytics','Аналитика по магазинам','Магазин1'),
]

async def run(base_url, output):
    if urlsplit(base_url).hostname not in {'localhost','127.0.0.1'}:
        raise ValueError('Only a local synthetic test server is allowed')
    output.mkdir(parents=True,exist_ok=True)
    app=FastAPI();app.include_router(router,prefix='/api')
    results=[]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client, async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--disable-dev-shm-usage'])
        try:
            resolver=await browser.new_page()
            await resolver.route('**/__pdf-fixtures',lambda route:route.fulfill(content_type='text/html',body='<html><body>Fixtures</body></html>'))
            await resolver.goto(base_url+'/__pdf-fixtures')
            await resolver.evaluate("async base=>{window.pdfFixture=(await import(base+'/tests/fixtures/pdfReports.ts')).pdfFixture}",base_url)
            for path,slug,title,last in CASES:
                context=await browser.new_context(accept_downloads=True,viewport={'width':1440,'height':1000})
                page=await context.new_page();errors=[];payloads=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                async def handle(route):
                    url=route.request.url
                    if urlsplit(url).hostname not in {'localhost','127.0.0.1'}:
                        return await route.abort()
                    if not urlsplit(url).path.startswith(urlsplit(base_url).path+'/api/'): return await route.continue_()
                    endpoint=url.split('/api',1)[1]
                    if endpoint=='/reports/pdf':
                        data=route.request.post_data_json;payloads.append(data)
                        response=await client.post('/api/reports/pdf',json=data)
                        return await route.fulfill(status=response.status_code,headers=dict(response.headers),body=response.content)
                    fixture=await resolver.evaluate('path=>window.pdfFixture(path)',endpoint)
                    await route.fulfill(content_type='application/json',body=json.dumps(fixture,ensure_ascii=False))
                await page.route('**/*',handle)
                await page.goto(base_url+'/reports/'+path)
                try:
                    await page.get_by_role('button',name='Скачать PDF',exact=True).wait_for(timeout=10000)
                except Exception:
                    print('PAGE ERROR',path,errors,await page.locator('body').inner_text(),flush=True)
                    raise
                if path=='product-sales': await page.get_by_role('button',name='Набор1',exact=True).click()
                await page.get_by_role('button',name='Сформировать отчёт',exact=True).click()
                button=page.get_by_role('button',name='Скачать PDF',exact=True)
                await page.wait_for_function("()=>!document.querySelector('.pdf-export-action button')?.disabled")
                if path=='seasonality':
                    await page.get_by_role('button',name='001',exact=True).click()
                    await page.get_by_text('Модель по всей истории',exact=True).wait_for()
                    await page.wait_for_function("()=>!document.querySelector('.pdf-export-action button')?.disabled")
                if path=='product-analytics':
                    await page.locator('.product-analytics-table tbody tr').first.click()
                    await page.get_by_role('heading',name='Тестовый товар 0',exact=True).wait_for()
                    await page.wait_for_function("()=>!document.querySelector('.pdf-export-action button')?.disabled")
                await page.screenshot(path=str(output/(slug+'-html.png')),full_page=True)
                button=page.get_by_role('button',name='Скачать PDF',exact=True).last
                async with page.expect_download(timeout=120000) as download:
                    await button.click()
                download=await download.value
                file=output/(slug+'.pdf');await download.save_as(file)
                assert download.suggested_filename.startswith(slug+'_')
                text=subprocess.run(['pdftotext','-raw',str(file),'-'],capture_output=True,text=True,check=True).stdout
                assert title in ' '.join(text.split()) and last in ' '.join(text.split()),(path,'Missing content',last)
                info=subprocess.run(['pdfinfo',str(file)],capture_output=True,text=True,check=True).stdout
                pages=int(next(line.split(':')[1] for line in info.splitlines() if line.startswith('Pages:')))
                assert pages>=1 and 'A4' in info
                assert not errors,(path,errors)
                assert len(payloads)==1
                assert 'Подготовка полного отчёта' not in payloads[0]['html']
                if path=='rfm': assert '20% ·' not in text or 'Новые' in text
                if path in {'rfm','product-sales','product-analytics','promotion-candidates'}:
                    rows=500 if path in {'rfm','promotion-candidates'} else 550
                    prefix='Тестовый клиент' if path=='rfm' else 'Тестовый кандидат' if path=='promotion-candidates' else 'Тестовый товар'
                    compact=' '.join(text.split())
                    assert all(f'{prefix} {i}' in compact for i in range(rows)),(path,'Missing rows')
                content_pages=text.split('\f')
                assert all(len(part.strip())>35 for part in content_pages[:-1]),(path,'Empty page')
                # Preserve an auditable sanitized-input snapshot and PDF raster.
                (output/(slug+'.html')).write_text(payloads[0]['html'])
                subprocess.run(['pdftoppm','-f','1','-singlefile','-scale-to','1400','-png',str(file),str(output/(slug+'-pdf'))],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                results.append({'report':slug,'status':'PASS','pages':pages,'bytes':file.stat().st_size,'svg_charts':payloads[0]['html'].count('recharts-surface'),'file':str(file)})
                print(json.dumps(results[-1],ensure_ascii=False),flush=True)
                await context.close()
        finally: await browser.close()
    (output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base-url',default='http://127.0.0.1:5174/vr/sales');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();asyncio.run(run(args.base_url.rstrip('/'),args.output))
