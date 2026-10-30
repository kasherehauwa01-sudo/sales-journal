"""Private HTML snapshot printing. No database, navigation or executable page code."""
import asyncio
import html
import os
import re
from html.parser import HTMLParser

from fastapi import HTTPException
from app.services.product_analytics_runtime import heavy_operation, ensure_headroom, available_memory

MAX_HTML_BYTES = 32 * 1024 * 1024
PRINT_CSS = """
@page { size: A4 landscape; margin: 18mm 10mm 17mm; }
html,body { margin:0!important; padding:0!important; width:auto!important; height:auto!important;
 font-family:'DejaVu Sans',Arial,sans-serif!important; color:#17243b; background:white!important; }
* { box-sizing:border-box; -webkit-print-color-adjust:exact!important; print-color-adjust:exact!important;
 animation:none!important; transition:none!important; }
.pdf-report { width:100%!important; padding:0!important; margin:0!important; }
.pdf-report * { max-height:none!important; text-overflow:clip!important; }
.pdf-report .panel { box-shadow:none!important; margin:10px 0!important; }
.pdf-report .table-wrap { overflow:visible!important; height:auto!important; }
table { width:100%!important; min-width:0!important; table-layout:fixed!important; border-collapse:collapse!important; }
thead { display:table-header-group!important; } tfoot { display:table-footer-group!important; }
tr { break-inside:avoid; } td,th { white-space:normal!important; overflow-wrap:anywhere!important;
 min-width:0!important; padding:5px!important; font-size:11px!important; line-height:1.35!important; }
.pdf-report section,.pdf-report .panel,.recharts-wrapper,.recharts-responsive-container {max-width:100%!important;}
.pdf-report svg { max-width:100%!important; overflow:visible; }
.recharts-wrapper,.recharts-responsive-container,.pdf-chart,.kpi { break-inside:avoid!important; }
.pdf-report .chart { height:auto!important; }
pre {white-space:pre-wrap!important;overflow-wrap:anywhere!important;}
h1,h2,h3 { break-after:avoid; } h1 {font-size:22px!important} h2 {font-size:16px!important}
.pdf-filters {font-size:11px; line-height:1.5; border:1px solid #d8e2f0; padding:10px; margin-bottom:12px;}
.pdf-table-band {margin:10px 0; font-size:10px; color:#53627b;}
"""
TAGS = set('div span p a label pre code h1 h2 h3 h4 h5 h6 section article main header footer details summary small strong b i em u s br hr ul ol li dl dt dd table thead tbody tfoot tr th td caption colgroup col img svg g path rect circle ellipse line polyline polygon text tspan defs lineargradient radialgradient stop clippath mask pattern title desc'.split())
VOID = {'img','br','hr','col'}
ATTRS = set('open class style title colspan rowspan scope width height viewbox preserveaspectratio x y x1 x2 y1 y2 dx dy d points fill stroke stroke-width stroke-dasharray stroke-linecap stroke-linejoin opacity fill-opacity stroke-opacity transform text-anchor dominant-baseline font-size font-family font-weight clip-path id offset stop-color stop-opacity gradientunits gradienttransform patternunits patterntransform r rx ry cx cy'.split())
SVG_CASE = {'viewbox':'viewBox','preserveaspectratio':'preserveAspectRatio','lineargradient':'linearGradient','radialgradient':'radialGradient','clippath':'clipPath','gradientunits':'gradientUnits','gradienttransform':'gradientTransform','patternunits':'patternUnits','patterntransform':'patternTransform'}


def safe_style(value):
    # Backslash escapes/comments can disguise URLs, imports and legacy expressions.
    if re.search(r'\\|/\*|@|expression\s*\(|behavior\s*:|-moz-binding', value, re.I):
        return ''
    without_fragments = re.sub(r'url\(\s*[\'\"]?#[\w:-]+[\'\"]?\s*\)', '', value, flags=re.I)
    return '' if re.search(r'url\s*\(', without_fragments, re.I) else value


class SnapshotParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.stack = []
        self.blocked = 0

    def handle_starttag(self, tag, attrs):
        if self.blocked or tag not in TAGS:
            if tag not in VOID:
                self.stack.append((tag, False))
                self.blocked += 1
            return
        safe = []
        for key, value in attrs:
            value = value or ''
            if key == 'src' and tag == 'img':
                if not re.fullmatch(r'data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+', value):
                    continue
            elif key not in ATTRS:
                continue
            if key == 'style': value = safe_style(value)
            if key in {'fill','stroke','clip-path'} and 'url' in value.lower():
                if not re.fullmatch(r'url\(#[\w:-]+\)', value): continue
            safe.append(f'{SVG_CASE.get(key,key)}="{html.escape(value, quote=True)}"')
        self.output.append('<' + SVG_CASE.get(tag,tag) + (' ' + ' '.join(safe) if safe else '') + '>')
        if tag not in VOID: self.stack.append((tag, True))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID: self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack: return
        if self.stack[-1][0] != tag:
            raise ValueError('Malformed snapshot')
        _, allowed = self.stack.pop()
        if allowed: self.output.append(f'</{SVG_CASE.get(tag,tag)}>')
        else: self.blocked -= 1

    def handle_data(self, data):
        if not self.blocked: self.output.append(html.escape(data))


def safe_document(snapshot):
    parser = SnapshotParser()
    try:
        parser.feed(snapshot)
        parser.close()
        if parser.stack: raise ValueError('Unclosed snapshot')
    except (ValueError, RecursionError) as exc:
        raise HTTPException(422, 'Некорректная печатная версия отчёта.') from exc
    if not parser.output: raise HTTPException(422, 'Печатная версия пуста.')
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'style-src \'unsafe-inline\'; img-src data:; font-src \'none\'">'
            f'<style>{PRINT_CSS}</style></head><body><main class="pdf-report">'
            + ''.join(parser.output) + '</main></body></html>')


async def render_pdf(snapshot, title, period):
    # Lazy import keeps ordinary reports independent of browser startup.
    from playwright.async_api import async_playwright, Error as BrowserError
    async with heavy_operation('report_pdf'):
        ensure_headroom()
        available = available_memory()
        if available is not None and available < 512 * 1024 * 1024:
            raise HTTPException(503, 'Недостаточно свободной памяти для PDF. Повторите позже.')
        browser = None
        try:
            async with asyncio.timeout(90):
                document = await asyncio.to_thread(safe_document, snapshot)
                async with async_playwright() as playwright:
                    try:
                        browser = await playwright.chromium.launch(
                            executable_path=os.environ.get('REPORT_PDF_CHROMIUM', '/usr/bin/chromium'),
                            headless=True,
                            args=['--disable-dev-shm-usage','--disable-gpu','--no-first-run'],
                        )
                        context = await browser.new_context(java_script_enabled=False,
                            service_workers='block', offline=True, viewport={'width':1120,'height':800})
                        await context.route('**/*', lambda route: route.abort())
                        page = await context.new_page()
                        await page.set_content(document, wait_until='load', timeout=15000)
                        await page.emulate_media(media='print')
                        heading = html.escape(title + ' · ' + period)
                        pdf = await page.pdf(format='A4', landscape=True, print_background=True,
                            prefer_css_page_size=True, display_header_footer=True,
                            header_template=f'<div style="font-family:Arial;font-size:8px;margin:0 10mm;color:#53627b">{heading}</div>',
                            footer_template='<div style="font-family:Arial;font-size:8px;width:100%;text-align:center;color:#53627b"><span class="pageNumber"></span> / <span class="totalPages"></span></div>')
                    finally:
                        if browser: await browser.close()
        except (TimeoutError, BrowserError):
            raise HTTPException(503, 'Не удалось сформировать PDF за отведённое время. Повторите позже.') from None
        return pdf
