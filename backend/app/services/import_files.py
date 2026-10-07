"""Жизненный цикл временных исходных файлов импорта."""
import logging
from pathlib import Path
from typing import Awaitable, Callable

log=logging.getLogger(__name__)


def import_temp_dir()->Path:
    """Каталог новых исходников: вне постоянного volume старых импортов."""
    from app.config import settings
    path=settings.import_temp_dir
    path.mkdir(parents=True,exist_ok=True)
    return path


async def with_temporary_source(path:Path,operation:Callable[[],Awaitable[None]])->None:
    """Выполняет импорт и удаляет источник независимо от его результата."""
    try:
        await operation()
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.exception("Не удалось удалить временный файл импорта %s",path)
            raise
