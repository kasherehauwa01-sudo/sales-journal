import asyncio
from io import BytesIO
from zipfile import ZipFile
from pathlib import Path
import time
import pytest
from PIL import Image
from pydantic import ValidationError
from openpyxl import load_workbook
from fastapi import HTTPException
from app.services import promotion_selected_export as export
from app.routers import promotion_candidates_report as router


def row(**overrides):
    return {
        "key": "code:001",
        "name": "Товар",
        "code": "001",
        "image_url": "https://catalog.test/a.png",
        "stock": 10,
        "aviatorov_stock": 2,
        **overrides,
    }


def test_selected_workbook_all_columns_and_embedded_photo(monkeypatch):
    calls = []

    def photo(value, directory, index, deadline):
        calls.append(value)
        path = Path(directory) / f"{index}.png"
        Image.new("RGB", (40, 30), "red").save(path)
        return path

    monkeypatch.setattr(export, "thumbnail", photo)
    monkeypatch.setattr(export, "ensure_headroom", lambda: None)
    result = export.excel_selected(export.SelectedExport(rows=[row(name="=1+1")]))
    with ZipFile(BytesIO(result.body)) as archive:
        assert (
            len([name for name in archive.namelist() if name.startswith("xl/media/")])
            == 1
        )
    book = load_workbook(BytesIO(result.body))
    try:
        sheet = book["Выбранные товары"]
        assert [cell.value for cell in sheet[1]] == export.HEADERS
        assert sheet.max_row == 2
        assert sheet["B2"].value == "=1+1" and sheet["B2"].data_type == "s"
        assert sheet["C2"].value == "001" and sheet["M2"].value == 2
        assert len(sheet._images) == 1
        assert book.sheetnames == ["Выбранные товары"]
        assert sheet["B2"].alignment.wrap_text is True
        assert sheet["B1"].font.bold is True
        assert sheet.column_dimensions["B"].width <= 50
    finally:
        book.close()
    assert calls == ["https://catalog.test/a.png"]


def test_missing_photo_is_not_replaced_by_link(monkeypatch):
    monkeypatch.setattr(export, "thumbnail", lambda *args: None)
    monkeypatch.setattr(export, "ensure_headroom", lambda: None)
    with pytest.raises(HTTPException) as error:
        export.excel_selected(export.SelectedExport(rows=[row()]))
    assert error.value.status_code == 502
    result = export.excel_selected(export.SelectedExport(rows=[row(image_url=None)]))
    book = load_workbook(BytesIO(result.body), read_only=True)
    try:
        assert book.sheetnames == ["Выбранные товары"]
        assert book["Выбранные товары"]["A2"].value == "Нет фото"
    finally:
        book.close()


def test_untrusted_origin_and_expired_budget_never_download(monkeypatch, tmp_path):
    monkeypatch.setattr(
        export.settings, "vrcatalog_api_url", "https://catalog.test/api"
    )

    def reject(*args):
        raise AssertionError("network must not run")

    monkeypatch.setattr(export, "build_opener", reject)
    assert (
        export.thumbnail(
            "http://127.0.0.1/secret", tmp_path, 1, time.perf_counter() + 10
        )
        is None
    )
    assert (
        export.thumbnail(
            "https://catalog.test/a.jpg", tmp_path, 1, time.perf_counter() - 1
        )
        is None
    )
    assert export.trusted_image_url("https://user:secret@catalog.test/a.jpg") is None
    assert (
        export.trusted_image_url("https://other.test/a.jpg")
        == "https://other.test/a.jpg"
    )


def test_selection_limits_and_memory_rejection(monkeypatch):
    with pytest.raises(ValidationError):
        export.SelectedExport(rows=[])
    with pytest.raises(ValidationError):
        export.SelectedExport(rows=[row()] * 301)

    def reject():
        raise HTTPException(503, "memory_headroom")

    monkeypatch.setattr(export, "ensure_headroom", reject)
    with pytest.raises(HTTPException):
        export.excel_selected(export.SelectedExport(rows=[row()]))


def test_thumbnail_download_is_bounded_and_resized(monkeypatch, tmp_path):
    monkeypatch.setattr(
        export.settings, "vrcatalog_api_url", "https://catalog.test/api"
    )
    monkeypatch.setattr(export, "ensure_headroom", lambda: None)
    data = BytesIO()
    Image.new("RGB", (200, 100), "blue").save(data, format="PNG")

    class Opener:
        def open(self, *args, **kwargs):
            return BytesIO(data.getvalue())

    monkeypatch.setattr(export, "public_address", lambda url: ("93.184.216.34", 443))
    monkeypatch.setattr(export, "build_opener", lambda *args: Opener())
    path = export.thumbnail(
        "https://catalog.test/a.png", tmp_path, 2, time.perf_counter() + 10
    )
    with Image.open(path) as image:
        assert image.size == (96, 48)
    monkeypatch.setattr(export, "MAX_IMAGE_BYTES", 1)
    assert (
        export.thumbnail(
            "https://catalog.test/a.png", tmp_path, 3, time.perf_counter() + 10
        )
        is None
    )
    assert not (tmp_path / "source-image").exists()


def test_endpoint_exports_snapshot_without_dataset_query(monkeypatch):
    async def reject(*args):
        raise AssertionError("must not recompute report")

    monkeypatch.setattr(router, "_dataset", reject)
    monkeypatch.setattr(export, "thumbnail", lambda *args: None)
    monkeypatch.setattr(export, "ensure_headroom", lambda: None)
    result = asyncio.run(
        router.export_selected(export.SelectedExport(rows=[row(image_url=None)]))
    )
    assert result.status_code == 200


def test_redirect_and_oversized_dimensions_are_rejected(monkeypatch, tmp_path):
    from urllib.error import HTTPError

    with pytest.raises(HTTPError):
        export.NoRedirect().redirect_request(
            export.Request("https://catalog.test/a.png"),
            None,
            302,
            "redirect",
            {},
            "http://127.0.0.1/",
        )
    monkeypatch.setattr(
        export.settings, "vrcatalog_api_url", "https://catalog.test/api"
    )
    monkeypatch.setattr(export, "ensure_headroom", lambda: None)
    data = BytesIO()
    Image.new("RGB", (20, 20), "blue").save(data, format="PNG")

    class Opener:
        def open(self, *args, **kwargs):
            return BytesIO(data.getvalue())

    monkeypatch.setattr(export, "public_address", lambda url: ("93.184.216.34", 443))
    monkeypatch.setattr(export, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(export, "MAX_IMAGE_PIXELS", 100)
    assert (
        export.thumbnail(
            "https://catalog.test/a.png", tmp_path, 1, time.perf_counter() + 10
        )
        is None
    )
    assert not list(tmp_path.iterdir())


def test_widths_name_wrapping_and_single_sheet(monkeypatch):
    monkeypatch.setattr(export, "ensure_headroom", lambda: None)
    monkeypatch.setattr(export, "thumbnail", lambda *args: None)
    data = export.SelectedExport(
        rows=[row(name="Название " * 30, category="Длинная категория", image_url=None)]
    )
    result = export.excel_selected(data)
    book = load_workbook(BytesIO(result.body))
    try:
        sheet = book["Выбранные товары"]
        assert book.sheetnames == ["Выбранные товары"]
        assert sheet.column_dimensions["B"].width == 50
        assert sheet["B2"].alignment.wrap_text
        assert sheet.row_dimensions[2].height > 24
        assert sheet.column_dimensions["D"].width == 13
        assert sheet["H2"].number_format == "#,##0.##"
        assert sheet["A2"].value == "Нет фото"
    finally:
        book.close()


def test_private_dns_rejected_and_valid_address_pinned(monkeypatch):
    monkeypatch.setattr(
        export.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError):
        export.public_address("https://cdn.test/a.png")
    monkeypatch.setattr(
        export.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    assert export.public_address("https://cdn.test/a.png") == ("93.184.216.34", 443)
    connected = []
    monkeypatch.setattr(
        export.socket,
        "create_connection",
        lambda address, *args: connected.append(address),
    )
    handler = export.PinnedHTTPS(("93.184.216.34", 443))

    def open_connection(factory, request, **kwargs):
        connection = factory("cdn.test", timeout=3, context=None)
        assert connection.host == "cdn.test"
        connection._create_connection(("127.0.0.1", 443), 3)

    monkeypatch.setattr(handler, "do_open", open_connection)
    handler.https_open(export.Request("https://cdn.test/a.png"))
    assert connected == [("93.184.216.34", 443)]


def test_public_redirect_is_followed_without_credentials(monkeypatch):
    from urllib.error import HTTPError

    monkeypatch.setattr(export, "public_address", lambda url: ("93.184.216.34", 443))
    calls = []

    class Opener:
        def open(self, request, **kwargs):
            assert not request.has_header("Authorization")
            calls.append(request.full_url)
            if len(calls) == 1:
                raise HTTPError(
                    request.full_url,
                    302,
                    "redirect",
                    {"Location": "https://cdn.test/a.png"},
                    BytesIO(),
                )
            return BytesIO(b"image")

    monkeypatch.setattr(export, "build_opener", lambda *args: Opener())
    with export.image_response(
        "https://catalog.test/a.png", time.perf_counter() + 10
    ) as result:
        assert result.read() == b"image"
    assert calls == ["https://catalog.test/a.png", "https://cdn.test/a.png"]


def test_export_width_caps_wrapped_reasons_and_unfilled_rows(monkeypatch):
    monkeypatch.setattr(export, "ensure_headroom", lambda: None)
    data = export.SelectedExport(
        rows=[
            row(
                name="Название " * 30,
                category="Категория длинная",
                reasons=["Причина " * 30],
                image_url=None,
            ),
            row(key="code:002", image_url=None),
        ]
    )
    book = load_workbook(BytesIO(export.excel_selected(data).body))
    try:
        sheet = book["Выбранные товары"]
        for column, width in {
            "B": 50,
            "G": 50,
            "D": 13,
            "K": 13,
            "M": 13,
            "O": 13,
        }.items():
            assert sheet.column_dimensions[column].width <= width
        assert sheet["G2"].alignment.wrap_text
        assert sheet["B2"].value == "Название " * 30
        assert sheet["G2"].value == "Причина " * 30
        assert sheet.row_dimensions[2].height > 24
        assert sheet.row_dimensions[1].height > 26
        for values in sheet.iter_rows(min_row=2):
            assert all(cell.fill.patternType is None for cell in values)
    finally:
        book.close()
