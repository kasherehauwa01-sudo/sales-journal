"""Жизненный цикл временных исходных файлов импорта."""
import logging
from pathlib import Path
from typing import Awaitable, Callable

log=logging.getLogger(__name__)


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
