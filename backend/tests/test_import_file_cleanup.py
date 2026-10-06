import asyncio

import pytest

from app.services.import_files import with_temporary_source


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
