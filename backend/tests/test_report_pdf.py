"""PDF contract/security checks; browser opt-in uses synthetic data only."""
import asyncio
import os
import subprocess
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from app.services import report_pdf as service
from app.routers import report_pdf as router


def test_snapshot_removes_executable_and_remote_content():
    document=service.safe_document('<h1>Выручка ₽</h1><script>alert(1)</script><iframe src="http://localhost/"></iframe><img src="file:///etc/passwd"><div onclick="x()" style="background:url(http://secret/)">Данные</div><svg><defs><clipPath id="clip"><rect width="10" height="10"></rect></clipPath></defs><path d="M0 0" clip-path="url(#clip)"></path></svg>')
    assert all(value not in document for value in ['<script','<iframe','file:///','http://secret','onclick','alert(1)'])
    assert all(value in document for value in ['clipPath','url(#clip)','Выручка ₽','Content-Security-Policy'])


@pytest.mark.parametrize('style', ['background:u\\72l(http://x)', '@import "http://x";', 'background:u/**/rl(http://x)', 'width:expression(x)', '-moz-binding:url(x)'])
def test_disguised_css_resource_urls_are_rejected(style):
    assert service.safe_style(style)==''


def test_malformed_snapshot_fails_instead_of_partial_pdf():
    with pytest.raises(HTTPException) as error: service.safe_document('<table><tr><td>Потеря</table>')
    assert error.value.status_code==422


def test_route_returns_private_attachment_without_database(monkeypatch):
    render=AsyncMock(return_value=b'%PDF-1.7\n'+b'x'*200)
    monkeypatch.setattr(router,'render_pdf',render)
    app=FastAPI();app.include_router(router.router)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            for report in router.REPORTS:
                result=await client.post('/reports/pdf',json={'report':report,'filename':report+'_2026-10-01_2026-10-30.pdf','period':'2026-10-01 — 2026-10-30','html':'<h1>Тест</h1>'})
                assert result.status_code==200 and result.content.startswith(b'%PDF')
                assert result.headers['cache-control']=='no-store, private'
                assert report in result.headers['content-disposition']
            for update in [{'report':'admin'},{'filename':'../secret.pdf'},{'html':''}]:
                payload={'report':'rfm-analysis','period':'Весь период','filename':'rfm.pdf','html':'<h1>Тест</h1>',**update}
                assert (await client.post('/reports/pdf',json=payload)).status_code==422
            monkeypatch.setattr(router,'MAX_HTML_BYTES',10)
            assert (await client.post('/reports/pdf',content=b'x'*11)).status_code==413
    asyncio.run(run());assert render.await_count==8


@pytest.mark.skipif(os.environ.get('RUN_PDF_BROWSER_TESTS')!='1',reason='Requires local Chromium; opt-in without DB/production')
def test_real_chromium_500_rows_and_cyrillic(tmp_path):
    snapshot='<h1>Выручка — Розница</h1><svg width="300" height="100" viewBox="0 0 300 100"><rect width="150" height="80" fill="#2878ff"></rect><text x="155" y="50">График</text></svg><table><thead><tr><th>Клиент</th><th>Сумма</th></tr></thead><tbody>'+''.join(f'<tr><td>Строка-{i:04}</td><td>{i} ₽</td></tr>' for i in range(500))+'</tbody></table>'
    pdf=asyncio.run(service.render_pdf(snapshot,'Тестовый отчёт','2026-10-01 — 2026-10-30'))
    path=tmp_path/'report.pdf';path.write_bytes(pdf)
    assert pdf.startswith(b'%PDF-')
    text=subprocess.run(['pdftotext','-layout',str(path),'-'],capture_output=True,text=True,check=True).stdout
    assert all(f'Строка-{i:04}' in text for i in range(500))
    assert all(value in text for value in ['Выручка','Розница','₽','График'])
    info=subprocess.run(['pdfinfo',str(path)],capture_output=True,text=True,check=True).stdout
    assert 'A4' in info
    assert int(next(line.split(':')[1] for line in info.splitlines() if line.startswith('Pages:')))>1
