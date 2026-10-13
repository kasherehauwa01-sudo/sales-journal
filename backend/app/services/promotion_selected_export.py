"""Bounded export of the already displayed/selected report snapshot."""

import tempfile
import ipaddress
import socket
from http.client import HTTPConnection, HTTPSConnection
from textwrap import wrap
import time
from io import BytesIO
from pathlib import Path
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urljoin
from urllib.request import (
    HTTPRedirectHandler,
    HTTPHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from fastapi import Response, HTTPException
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from app.config import settings
from app.services.catalog_sync import sync_image_url
from app.services.product_analytics_runtime import ensure_headroom

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 4_000_000
IMAGE_BUDGET_SECONDS = 15
HEADERS = [
    "Фото",
    "Товар",
    "Код / артикул",
    "Категория",
    "Сезонность",
    "Сезонный приоритет",
    "Причины",
    "30 дней",
    "Пред. 30",
    "Изменение, %",
    "Изменение категории, %",
    "Остаток",
    "Остатки Авиаторов Зал+Склад",
    "Запас, мес.",
    "Без продажи, дней",
    "Статус",
    "Менеджер",
]


class Season(BaseModel):
    status: str = Field(default="Недостаточно данных", max_length=100)
    priority: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)


class SelectedRow(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    key: str = Field(max_length=1000)
    name: str = Field(max_length=1000)
    code: str | None = Field(default=None, max_length=500)
    article: str | None = Field(default=None, max_length=500)
    category: str = Field(default="", max_length=1000)
    catalog_manager: str | None = Field(default=None, max_length=1000)
    image_url: str | None = Field(default=None, max_length=2048)
    seasonality: Season | None = None
    reasons: list[Annotated[str, Field(max_length=1000)]] = Field(
        default_factory=list, max_length=30
    )
    units_30: float = 0
    units_previous_30: float = 0
    units_change_percent: float | None = None
    category_change_percent: float | None = None
    stock: float | None = None
    aviatorov_stock: float | None = None
    stock_months: float | None = None
    days_without_sales: int | None = None
    status: str = Field(default="", max_length=100)


class SelectedExport(BaseModel):
    rows: list[SelectedRow] = Field(min_length=1, max_length=300)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "Redirect blocked", headers, fp)


def trusted_image_url(value):
    url = sync_image_url(value)
    if not url:
        return None
    parsed = urlsplit(url)
    if (
        parsed.port not in (None, 80, 443)
        or parsed.hostname == "localhost"
        or parsed.hostname.endswith(".localhost")
    ):
        return None
    try:
        if not ipaddress.ip_address(parsed.hostname).is_global:
            return None
    except ValueError:
        pass  # DNS names are checked and pinned immediately before connecting.
    return url


def public_address(url):
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(
        not ipaddress.ip_address(item[4][0]).is_global for item in addresses
    ):
        raise ValueError("non_public_image_address")
    return addresses[0][4][0], port


class PinnedHTTP(HTTPHandler):
    def __init__(self, address):
        super().__init__()
        self.address = address

    def http_open(self, request):
        def connection(host, **kwargs):
            result = HTTPConnection(host, **kwargs)
            result._create_connection = (
                lambda _, timeout, source=None: socket.create_connection(
                    self.address, timeout, source
                )
            )
            return result

        return self.do_open(connection, request)


class PinnedHTTPS(HTTPSHandler):
    def __init__(self, address):
        super().__init__()
        self.address = address

    def https_open(self, request):
        def connection(host, **kwargs):
            result = HTTPSConnection(host, **kwargs)
            result._create_connection = (
                lambda _, timeout, source=None: socket.create_connection(
                    self.address, timeout, source
                )
            )
            return result

        # HTTPSConnection still checks the certificate and uses the original
        # hostname for SNI; only the already-validated connection IP is pinned.
        return self.do_open(
            connection,
            request,
            context=self._context,
        )


def image_response(url, deadline):
    for attempt in range(4):
        url = trusted_image_url(url)
        if not url or time.perf_counter() >= deadline:
            raise ValueError("image_unavailable")
        address = public_address(url)
        timeout = min(3, max(0.1, deadline - time.perf_counter()))
        request = Request(
            url,
            headers={"Accept": "image/*", "User-Agent": "SalesJournal-selected-export"},
        )
        try:
            return build_opener(
                ProxyHandler({}),
                PinnedHTTP(address),
                PinnedHTTPS(address),
                NoRedirect(),
            ).open(request, timeout=timeout)
        except HTTPError as error:
            location = error.headers.get("Location") if error.headers else None
            error.close()
            if (
                error.code not in (301, 302, 303, 307, 308)
                or not location
                or attempt == 3
            ):
                raise
            redirected = urljoin(url, location)
            if (
                urlsplit(url).scheme == "https"
                and urlsplit(redirected).scheme != "https"
            ):
                raise ValueError("image_redirect_downgrade")
            url = redirected
    raise ValueError("image_redirect_limit")


def thumbnail(value, directory, index, deadline):
    url = trusted_image_url(value)
    if not url or time.perf_counter() >= deadline:
        return None
    raw_path = Path(directory) / "source-image"
    target = Path(directory) / f"image-{index}.png"
    try:
        with image_response(url, deadline) as response, raw_path.open("wb") as output:
            size = 0
            while True:
                ensure_headroom()
                if time.perf_counter() >= deadline:
                    return None
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    return None
                output.write(chunk)
        ensure_headroom()
        with Image.open(raw_path) as picture:
            if picture.width * picture.height > MAX_IMAGE_PIXELS:
                return None
            picture.thumbnail((96, 72))
            if picture.mode not in ("RGB", "RGBA"):
                picture = picture.convert("RGB")
            picture.save(target, format="PNG")
        return target
    except (
        OSError,
        ValueError,
        HTTPError,
        URLError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ):
        return None
    finally:
        raw_path.unlink(missing_ok=True)


def safe_cell(sheet, value):
    cell = WriteOnlyCell(sheet, value=value)
    if isinstance(value, str):
        cell.data_type = "s"  # Product names must not become Excel formulas.
    return cell


def row_values(row):
    season = row.seasonality or Season()
    return [
        "",
        row.name,
        row.code or row.article or "—",
        row.category,
        season.status,
        season.priority,
        ", ".join(row.reasons),
        row.units_30,
        row.units_previous_30,
        row.units_change_percent,
        row.category_change_percent,
        row.stock,
        row.aviatorov_stock,
        row.stock_months,
        row.days_without_sales,
        row.status,
        row.catalog_manager,
    ]


def display(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def excel_selected(data):
    ensure_headroom()
    book = Workbook(write_only=True)
    try:
        with tempfile.TemporaryDirectory(prefix="promotion-selected-") as directory:
            sheet = book.create_sheet("Выбранные товары")
            sheet.freeze_panes = "D2"
            widths = [len(header) + 2 for header in HEADERS]
            for row in data.rows:
                for index, value in enumerate(row_values(row)):
                    widths[index] = max(
                        widths[index],
                        max(
                            (len(line) for line in display(value).splitlines()),
                            default=0,
                        )
                        + 2,
                    )
            widths[0] = 15  # 96-pixel photograph.
            limits = {1: 50, 6: 50, 3: 13, 10: 13, 12: 13, 14: 13}
            for column, limit in limits.items():
                widths[column] = min(limit, widths[column])
            for index, width in enumerate(widths, 1):
                sheet.column_dimensions[get_column_letter(index)].width = min(
                    255, width
                )
            border = Border(bottom=Side(style="thin", color="DCE2EC"))
            headers = []
            for value in HEADERS:
                cell = safe_cell(sheet, value)
                cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="243451")
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                headers.append(cell)
            sheet.row_dimensions[1].height = max(
                26,
                max(
                    len(wrap(header, width=max(1, int(widths[column]) - 2)))
                    for column, header in enumerate(HEADERS)
                )
                * 15
                + 8,
            )
            sheet.append(headers)
            deadline = time.perf_counter() + IMAGE_BUDGET_SECONDS
            images = {}
            for index, row in enumerate(data.rows, 2):
                ensure_headroom()
                if row.image_url not in images:
                    images[row.image_url] = thumbnail(
                        row.image_url, directory, index, deadline
                    )
                path = images[row.image_url]
                if row.image_url and not path:
                    raise HTTPException(
                        502,
                        "Не удалось встроить фото в Excel. Повторите выгрузку или выберите меньше товаров.",
                    )
                values = row_values(row)
                wrapped_lines = max(
                    sum(
                        max(1, len(wrap(line, width=max(1, int(widths[column]) - 2))))
                        for line in display(values[column]).split("\n")
                    )
                    for column in limits
                )
                sheet.row_dimensions[index].height = min(
                    409, max(56 if path else 24, 15 * wrapped_lines + 8)
                )
                if path:
                    sheet.add_image(ExcelImage(path), f"A{index}")
                else:
                    values[0] = "Нет фото"
                cells = []
                for column, value in enumerate(values):
                    cell = safe_cell(sheet, value)
                    cell.font = Font(name="Calibri", size=11)
                    cell.border = border
                    cell.alignment = Alignment(
                        vertical="center",
                        horizontal=(
                            "right" if isinstance(value, (float, int)) else "left"
                        ),
                        wrap_text=column in limits,
                    )
                    if isinstance(value, (float, int)):
                        cell.number_format = "#,##0.##"
                    cells.append(cell)
                sheet.append(cells)
            sheet.auto_filter.ref = (
                f"A1:{get_column_letter(len(HEADERS))}{len(data.rows)+1}"
            )
            output = BytesIO()
            ensure_headroom()
            book.save(output)
            ensure_headroom()
            return Response(
                output.getvalue(),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": "attachment; filename=promotion-candidates-selected.xlsx"
                },
            )
    finally:
        for sheet in book.worksheets:
            try:
                if not sheet.closed:
                    sheet.close()
                if sheet._writer:
                    sheet._writer.cleanup()
            except (OSError, ValueError):
                pass
        book.close()
