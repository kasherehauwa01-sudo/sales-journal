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
    asyncio.run(run());assert render.await_count==len(router.REPORTS)


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


def test_json_body_is_released_before_rendering(monkeypatch):
    import weakref
    bodies=[]
    class Body(bytearray):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            bodies.append(weakref.ref(self))
    monkeypatch.setattr(router,'bytearray',Body,raising=False)
    async def render(snapshot,title,period):
        assert bodies and bodies[-1]() is None
        assert snapshot=='<h1>Полные данные</h1>'
        return b'%PDF-'+b'x'*200
    monkeypatch.setattr(router,'render_pdf',render)
    app=FastAPI();app.include_router(router.router)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            result=await client.post('/reports/pdf',json={'report':'rfm-analysis','filename':'rfm.pdf','period':'Тест','html':'<h1>Полные данные</h1>'})
            assert result.status_code==200
            assert result.headers['content-disposition']=='attachment; filename="rfm.pdf"'
    asyncio.run(run())


def test_pdf_memory_floor_is_kept_and_reports_safe_metrics(monkeypatch,caplog):
    import logging
    monkeypatch.setattr(service,'available_memory',lambda:511*1024*1024)
    with caplog.at_level(logging.INFO):
        with pytest.raises(HTTPException) as failure:
            asyncio.run(service.render_pdf('PRIVATE REPORT','title','period'))
    assert failure.value.status_code==503
    assert failure.value.headers['X-Sales-PDF-Error']=='memory_headroom'
    assert '512' in failure.value.detail
    assert any(getattr(r,'pdf_stage',None)=='preflight' for r in caplog.records)
    assert 'PRIVATE REPORT' not in caplog.text


@pytest.mark.parametrize('kind,expected',[('launch','browser_unavailable'),('timeout','timeout'),('print','browser_failed')])
def test_pdf_failures_distinguish_browser_and_timeout_without_private_details(monkeypatch,caplog,kind,expected):
    from contextlib import asynccontextmanager
    import logging
    from unittest.mock import Mock
    from playwright import async_api
    from app.services import pdf_images
    monkeypatch.setattr(service,'available_memory',lambda:1024*1024*1024)
    monkeypatch.setattr(pdf_images,'image_document',lambda _: '<p>Full data</p>')
    browser=Mock();browser.close=AsyncMock()
    page=Mock();page.set_content=AsyncMock();page.emulate_media=AsyncMock()
    page.pdf=AsyncMock(side_effect=async_api.Error('PRIVATE REPORT TOKEN'))
    context=Mock();context.route=AsyncMock();context.new_page=AsyncMock(return_value=page)
    browser.new_context=AsyncMock(return_value=context)
    launcher=AsyncMock(return_value=browser)
    if kind!='print':launcher.side_effect=TimeoutError('PRIVATE REPORT TOKEN') if kind=='timeout' else async_api.Error('PRIVATE REPORT TOKEN')
    @asynccontextmanager
    async def playwright():
        yield Mock(chromium=Mock(launch=launcher))
    monkeypatch.setattr(async_api,'async_playwright',playwright)
    with caplog.at_level(logging.INFO):
        with pytest.raises(HTTPException) as failure:
            asyncio.run(service.render_pdf('PRIVATE REPORT TOKEN','title','period'))
    assert failure.value.status_code==503
    assert failure.value.headers['X-Sales-PDF-Error']==expected
    assert 'PRIVATE' not in failure.value.detail and 'PRIVATE' not in caplog.text
    assert any(getattr(r,'pdf_reason',None)==expected for r in caplog.records)
    if kind=='print':browser.close.assert_awaited_once()


@pytest.mark.parametrize('failure',[False,True])
def test_browser_environment_is_private_scoped_and_cleaned(monkeypatch,failure):
    from pathlib import Path
    import stat
    variables=('HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_RUNTIME_DIR','CHROME_CONFIG_HOME')
    for key in variables:monkeypatch.setenv(key,'/proc/1/pdf-unwritable-'+key)
    before={key:os.environ[key] for key in variables}
    paths=[]
    def enter():
        with service.chromium_environment() as environment:
            for key in variables:
                path=Path(environment[key]);paths.append(path)
                assert path.is_dir() and stat.S_IMODE(path.stat().st_mode)==0o700
                assert environment[key]!=before[key]
            assert len({path.parent for path in paths})==1
            if failure:raise RuntimeError('synthetic cleanup')
    if failure:
        with pytest.raises(RuntimeError):enter()
    else:enter()
    assert all(not path.exists() for path in paths)
    assert all(os.environ[key]==before[key] for key in variables)


def test_browser_environment_failure_is_controlled_503(monkeypatch,caplog):
    from contextlib import contextmanager
    from app.services import pdf_images
    monkeypatch.setattr(service,'available_memory',lambda:1024*1024*1024)
    monkeypatch.setattr(pdf_images,'image_document',lambda _: '<p>Full data</p>')
    @contextmanager
    def unavailable():
        raise PermissionError('PRIVATE DIRECTORY')
        yield
    monkeypatch.setattr(service,'chromium_environment',unavailable)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(service.render_pdf('PRIVATE REPORT','title','period'))
    assert failure.value.status_code==503
    assert failure.value.headers['X-Sales-PDF-Error']=='browser_unavailable'
    assert 'PRIVATE' not in caplog.text and 'PRIVATE' not in failure.value.detail


@pytest.mark.skipif(os.environ.get('RUN_PDF_BROWSER_TESTS')!='1',reason='Local Chromium opt-in, no production')
def test_real_pdf_with_unwritable_parent_home_and_scoped_cleanup(monkeypatch,tmp_path):
    from pathlib import Path
    from contextlib import contextmanager
    for key in ('HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_RUNTIME_DIR','CHROME_CONFIG_HOME'):
        monkeypatch.setenv(key,'/proc/1/pdf-unwritable-'+key)
    original=service.chromium_environment
    paths=[]
    @contextmanager
    def observed():
        with original() as environment:
            paths.append(Path(environment['HOME']).parent)
            yield environment
    monkeypatch.setattr(service,'chromium_environment',observed)
    pdf=asyncio.run(service.render_pdf('<h1>PDF HOME CHECK</h1><p>123.45</p>','test','test'))
    file=tmp_path/'home.pdf';file.write_bytes(pdf)
    text=subprocess.run(['pdftotext',str(file),'-'],capture_output=True,text=True,check=True).stdout
    assert 'PDF HOME CHECK' in text and '123.45' in text
    assert paths and all(not path.exists() for path in paths)
