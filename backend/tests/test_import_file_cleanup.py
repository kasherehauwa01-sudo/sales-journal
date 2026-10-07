import asyncio
from pathlib import Path

import pytest

from app.services.import_files import with_temporary_source
from app.importer.parser import normalize_sale,read_sales


def source(tmp_path,suffix=".xlsx"):
    path=tmp_path/f"upload{suffix}"
    path.write_bytes(b"temporary source")
    return path


def test_successful_import_removes_source_and_keeps_journal_metadata(tmp_path):
    path=source(tmp_path)
    journal={"filename":"sales.xlsx","file_size":path.stat().st_size,"status":"queued","added_rows":0}

    async def operation():
        journal.update(status="completed",added_rows=12)

    asyncio.run(with_temporary_source(path,operation))

    assert not path.exists()
    assert journal=={"filename":"sales.xlsx","file_size":16,"status":"completed","added_rows":12}


@pytest.mark.parametrize("suffix",[".xls",".xlsx",".html"])
def test_parser_error_removes_every_supported_source(tmp_path,suffix):
    path=source(tmp_path,suffix)

    async def operation():
        raise ValueError("Ошибка разбора")

    with pytest.raises(ValueError,match="Ошибка разбора"):
        asyncio.run(with_temporary_source(path,operation))
    assert not path.exists()


def test_import_error_after_partial_progress_removes_source_and_keeps_progress(tmp_path):
    path=source(tmp_path,".html")
    journal={"filename":"sales.html","file_size":path.stat().st_size,"status":"processing","processed_rows":0}

    async def operation():
        journal["processed_rows"]=7
        journal["status"]="failed"
        journal["error_text"]="Ошибка записи"
        raise RuntimeError("Ошибка записи")

    with pytest.raises(RuntimeError,match="Ошибка записи"):
        asyncio.run(with_temporary_source(path,operation))

    assert not path.exists()
    assert journal["filename"]=="sales.html"
    assert journal["file_size"]==16
    assert journal["processed_rows"]==7
    assert journal["status"]=="failed"
    assert journal["error_text"]=="Ошибка записи"


def test_real_html_sales_survive_after_source_is_deleted(tmp_path):
    path=tmp_path/"sales.html"
    path.write_text('''<html><table>
    <tr><td>Дата</td><td>№ Док.</td><td>Клиент</td><td>Подразделение</td><td>Сумма</td><td>Товары</td></tr>
    <tr><td>06.10.2026</td><td>РН-100</td><td>ООО Тест</td><td>Европа</td><td>1250,50</td><td>Чайник</td></tr>
    </table></html>''',encoding="utf-8")
    journal={"filename":"sales.html","file_size":path.stat().st_size,"status":"processing"};sales=[]

    async def operation():
        _,rows=read_sales(path)
        sales.extend(normalize_sale(raw) for _,raw in rows)
        journal["status"]="completed";journal["added_rows"]=len(sales)

    asyncio.run(with_temporary_source(path,operation))

    assert not path.exists()
    assert journal["filename"]=="sales.html"
    assert journal["file_size"]>0
    assert journal["status"]=="completed"
    assert journal["added_rows"]==1
    assert sales[0]["document_number"]=="РН-100"
    assert str(sales[0]["total_amount"])=="1250.50"


def test_real_html_parser_error_removes_source_but_keeps_journal(tmp_path):
    path=tmp_path/"broken.html";path.write_text("<html><table><tr><td>неизвестный отчет</td></tr></table></html>")
    journal={"filename":"broken.html","file_size":path.stat().st_size,"status":"processing"}

    async def operation():
        try:read_sales(path)
        except Exception as exc:
            journal["status"]="failed";journal["error_text"]=str(exc)
            raise

    with pytest.raises(ValueError,match="Не найдена строка заголовков"):
        asyncio.run(with_temporary_source(path,operation))

    assert not path.exists()
    assert journal["filename"]=="broken.html"
    assert journal["file_size"]>0
    assert journal["status"]=="failed"
    assert "Не найдена строка заголовков" in journal["error_text"]


def test_new_imports_use_non_persistent_container_directory():
    root=Path(__file__).parents[2]
    compose=(root/"docker-compose.yml").read_text(encoding="utf-8")
    config=(root/"backend/app/config.py").read_text(encoding="utf-8")
    assert "IMPORT_TEMP_DIR: /tmp/sales-journal-imports" in compose
    assert "/tmp/sales-journal-imports:rw,noexec,nosuid,nodev" in compose
    assert 'import_temp_dir: Path = Path("/tmp/sales-journal-imports")' in config
