"""Local synthetic CORS/PDF regression. No production requests or DB/lifespan."""
import argparse
import asyncio
from contextlib import contextmanager
from io import BytesIO
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from pathlib import Path
import subprocess
from unittest.mock import patch
from urllib.parse import urlsplit

from PIL import Image
from fastapi import FastAPI
import httpx
from playwright.async_api import async_playwright
from app.routers.report_pdf import router
from app.services import pdf_images

async def run(base, output):
    if urlsplit(base).hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('local synthetic tests only')
    output.mkdir(parents=True, exist_ok=True)
    data=BytesIO();Image.new('RGB',(40,30),'red').save(data,format='PNG');png=data.getvalue()
    class Photos(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type','image/png')
            self.send_header('Content-Length',str(len(png)))
            if self.path=='/cors.png':self.send_header('Access-Control-Allow-Origin','*')
            self.end_headers();self.wfile.write(png)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Photos)
    Thread(target=server.serve_forever,daemon=True).start()
    photo_origin=f'http://127.0.0.1:{server.server_port}'
    fetched=[]
    @contextmanager
    def synthetic_fetch(url, deadline):
        assert url=='https://kvasmix.ru/vr/catalog/media/pdf-cors-test.png'
        fetched.append(url)
        stream=BytesIO(png);stream.headers={'Content-Type':'image/png'}
        with stream:yield stream
    app=FastAPI();app.include_router(router,prefix='/api')
    async with async_playwright() as pw, httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        browser=await pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--disable-dev-shm-usage'])
        try:
            page=await browser.new_page(permissions=['local-network-access'])
            async def route(request):
                url=request.request.url
                if url.endswith('/__image-test'):
                    return await request.fulfill(content_type='text/html',body='<html><head><meta charset="utf-8"></head><body></body></html>')
                if url.endswith('/same-origin.png') or url=='https://kvasmix.ru/vr/catalog/media/pdf-cors-test.png':
                    headers={'Access-Control-Allow-Origin':'https://not-allowed.invalid'} if url.startswith('https://kvasmix.ru/') else {}
                    return await request.fulfill(content_type='image/png',body=png,headers=headers)
                if url.endswith('/broken.png'):
                    return await request.fulfill(status=404,body='missing')
                if urlsplit(url).hostname not in {'localhost','127.0.0.1'}:
                    return await request.abort()
                await request.continue_()
            await page.route('**/*',route)
            await page.goto(base+'/__image-test')
            captured=await page.evaluate('''async ({base,photo_origin})=>{
                const {captureReport}=await import(base+'/src/services/reportPdf.ts');
                const root=document.createElement('div');root.className='page';
                const sources=[base+'/same-origin.png',photo_origin+'/cors.png',photo_origin+'/no-cors.png','https://kvasmix.ru/vr/catalog/media/pdf-cors-test.png',base+'/broken.png','file:///not-allowed'];
                root.innerHTML='<h1>Фотографии и показатели</h1><svg width="200" height="80"><rect width="100" height="40" fill="red"/></svg><table><tbody>'+Array.from({length:500},(_,i)=>`<tr><td>Товар-${String(i).padStart(4,'0')}</td><td><img width="40" height="30" src="${sources[i%6]}"></td><td>${i}.25</td></tr>`).join('')+'</tbody></table>';
                document.body.append(root);
                await Promise.all([...root.querySelectorAll('img')].map(image=>image.decode().catch(()=>{})));
                return await captureReport(root,{date_from:'2026-01-01'},new AbortController().signal);
            }''',{'base':base,'photo_origin':photo_origin})
            (output/'captured.html').write_text(captured)
            assert captured.count('<tr')==500
            assert captured.count('data:image/png;base64,')>=166, 'same-origin and CORS photos must survive'
            with patch.object(pdf_images,'image_response',synthetic_fetch):
                preview=pdf_images.image_document(captured)
                assert preview.count('data:image/png;base64,')>=249, 'trusted Catalog photos must survive without CORS'
                assert 'Фото недоступно' in preview
                assert 'https://' not in preview and 'data-pdf-image' not in preview
                assert len(fetched)==1, 'duplicates should share one bounded download'
                response=await client.post('/api/reports/pdf',json={'report':'promotion-candidates','period':'Тест','filename':'photos.pdf','html':captured})
            assert response.status_code==200,response.text[:100] if response.status_code!=200 else ''
            path=output/'photos.pdf';path.write_bytes(response.content)
            text=subprocess.run(['pdftotext','-raw',str(path),'-'],check=True,capture_output=True,text=True).stdout
            assert all(f'Товар-{i:04}' in text for i in range(500))
            assert 'Фото недоступно' in text and '499.25' in text
            images=subprocess.run(['pdfimages','-list',str(path)],check=True,capture_output=True,text=True).stdout
            image_count=sum(line.split()[2]=='image' for line in images.splitlines()[2:])
            assert image_count==preview.count('data:image/png;base64,'),'All readable photos must be embedded in the actual PDF'
            # Same renderer, no photos: no download or placeholder needed.
            before=len(fetched)
            with patch.object(pdf_images,'image_response',synthetic_fetch):
                plain=await client.post('/api/reports/pdf',json={'report':'sales-dynamics','period':'Тест','filename':'plain.pdf','html':'<h1>Без фотографий</h1><table><tr><td>Итого</td><td>123.45</td></tr></table>'})
            assert plain.status_code==200 and len(fetched)==before
            (output/'plain.pdf').write_bytes(plain.content)
            print(json.dumps({'status':'PASS','rows':500,'readable_frontend_photos':captured.count('data:image/png;base64,'),'embedded_photos':image_count,'photo_pdf_bytes':len(response.content),'no_photos':'PASS'},ensure_ascii=False))
        finally:
            await browser.close()
            server.shutdown();server.server_close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base-url',default='http://127.0.0.1:5174/vr/sales');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();asyncio.run(run(args.base_url.rstrip('/'),args.output))
