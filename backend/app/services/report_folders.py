"""Catalog folder selection shared by reports; None and an empty match differ."""
import asyncio
import hashlib
import time
from collections import OrderedDict
from typing import Annotated

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func

from app.models import SaleItem
from app.services import vrcatalog

FolderPairs = tuple[tuple[str, str], ...] | None
_cache: OrderedDict[tuple[str, ...], tuple[float, tuple[tuple[str, str], ...]]] = OrderedDict()
_pending: dict[tuple[str, ...], asyncio.Task] = {}
MAX_PRODUCTS = 100_000


class FolderRequest(BaseModel):
    folders: list[str] = Field(default_factory=list, max_length=100)


async def folder_paths():
    def validate(value):
        if not isinstance(value, list) or any(not isinstance(path, str) for path in value):
            raise ValueError("invalid folders")
    try:
        return await vrcatalog._cached_integration_get("filters/folders", validate=validate)
    except vrcatalog.VrCatalogError as exc:
        raise HTTPException(502, "Не удалось загрузить папки CatalogVR") from exc


async def resolve_folders(paths: list[str] | None) -> FolderPairs:
    if not paths:
        return None
    if len(paths) > 100 or any(not isinstance(p, str) or not p.strip() or len(p) > 4000 for p in paths):
        raise HTTPException(422, "Некорректный список папок")
    key = tuple(sorted(set(paths)))
    saved = _cache.get(key)
    if saved and time.monotonic() - saved[0] < 300:
        _cache.move_to_end(key)
        return saved[1]
    task = _pending.get(key)
    if task is None:
        task = asyncio.create_task(_load_folders(key))
        _pending[key] = task
        def completed(done):
            _pending.pop(key, None)
            if not done.cancelled():
                done.exception()  # Retrieve exceptions even when every caller was cancelled.
        task.add_done_callback(completed)
    return await asyncio.shield(task)


async def _load_folders(key):
    pairs = set()
    offset = 0
    page = 1
    expected_total = None
    try:
        # Use the same endpoint and prefix semantics as Catalog's FolderTree.
        # The integration category tree is a different directory.
        while True:
            payload = await asyncio.to_thread(vrcatalog._integration_get, "products/search", {
                "folder": key, "page": page, "pageSize": 100,
                "sort": "id", "order": "asc", "inStockOnly": "false", "excludeYyy": "false",
            })
            if not isinstance(payload, dict) or not isinstance(payload.get("pagination"), dict):
                raise ValueError("invalid folder page")
            total = payload["pagination"].get("totalItems")
            if not isinstance(total, int) or total < 0 or (expected_total is not None and total != expected_total):
                raise ValueError("folder contents changed during pagination")
            if total > MAX_PRODUCTS:
                raise HTTPException(422, "Выберите более узкую папку: слишком много товаров")
            expected_total = total
            rows = payload.get("items")
            if not isinstance(rows, list) or any(not isinstance(row, dict) or "code" not in row for row in rows):
                raise ValueError("invalid folder products")
            for row in rows:
                code, article = (str(row.get(field) or "").strip().lower() for field in ("code", "article"))
                if code or article:
                    pairs.add((code, article))
            offset += len(rows)
            if offset > MAX_PRODUCTS:
                raise HTTPException(422, "Выберите более узкую папку: слишком много товаров")
            if offset == total:
                break
            if len(rows) != 100 or offset > total:
                raise ValueError("incomplete folder page")
            page += 1
    except HTTPException:
        raise
    except Exception as exc:
        # Never return an unfiltered report on an unavailable or malformed source.
        raise HTTPException(502, "Не удалось получить товары выбранных папок CatalogVR") from exc
    result = tuple(sorted(pairs))
    _cache[key] = (time.monotonic(), result)
    _cache.move_to_end(key)
    while len(_cache) > 16 or sum(len(value[1]) for value in _cache.values()) > MAX_PRODUCTS:
        _cache.popitem(last=False)
    return result


async def folder_selection(folders: Annotated[list[str] | None, Query(max_length=100)] = None) -> FolderPairs:
    return await resolve_folders(folders)


def folder_item_condition(pairs: FolderPairs, code=SaleItem.code, article=SaleItem.article):
    if pairs is None:
        return True
    from app.services.report_filters import array_membership
    normalize = lambda column: func.lower(func.trim(func.coalesce(column, "")))
    # Length-prefix keeps the full code/article pair unambiguous in one array bind.
    identity = func.concat(func.length(normalize(code)), ":", normalize(code), normalize(article))
    return array_membership(identity, [f"{len(c)}:{c}{a}" for c, a in pairs], parameter_name="report_folder_pairs")


def folder_revision(pairs: FolderPairs):
    return hashlib.sha256(repr(pairs).encode()).hexdigest()
