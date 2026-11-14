"""Confirmed local hierarchy and monthly/store snapshot profile math."""

import json
from collections import defaultdict
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select
from app.models import CatalogProduct, CatalogLookup

UNKNOWN = "Без данных об иерархии"
BATCH = 500
MAX_GROUPS = 5000


def branch(category_id, category, subcategory, legacy):
    section = (subcategory or legacy or "").strip()
    confirmed = (
        category_id is not None
        and bool((category or "").strip())
        and bool((subcategory or "").strip())
    )
    parent = f"category:{category_id}" if confirmed else "unknown"
    name = section or "Без сопоставления"
    return dict(
        key=json.dumps([parent, name], ensure_ascii=False, separators=(",", ":")),
        parent_key=parent,
        parent=category.strip() if confirmed else UNKNOWN,
        name=name,
    )


async def directory(db):
    rows = (
        await db.execute(
            select(
                CatalogProduct.category_id,
                CatalogProduct.category,
                CatalogProduct.subcategory,
                CatalogProduct.legacy_category,
            )
            .where(
                select(CatalogLookup.identity)
                .where(
                    CatalogLookup.product_id == CatalogProduct.product_id,
                    CatalogLookup.status == "matched",
                )
                .exists()
            )
            .distinct()
            .limit(MAX_GROUPS + 1)
        )
    ).all()
    if len(rows) > MAX_GROUPS:
        raise HTTPException(503, "Справочник слишком велик для безопасного расчёта.")
    items = {b["key"]: b for row in rows if (b := branch(*row))}
    unknown = branch(None, None, None, None)
    items.setdefault(unknown["key"], unknown)
    parents = {}
    for b in sorted(
        items.values(), key=lambda x: (x["parent"].casefold(), x["name"].casefold())
    ):
        p = parents.setdefault(
            b["parent_key"],
            {"id": b["parent_key"], "name": b["parent"], "children": []},
        )
        p["children"].append(
            {"id": b["key"], "value": b["key"], "name": b["name"], "children": []}
        )
    return items, list(parents.values())


REGULARITY = Decimal("0.90")


def coverage_method(recorded_days, calendar_days, confirmed=False):
    return (
        "confirmed"
        if confirmed
        else (
            "estimated"
            if Decimal(recorded_days) / calendar_days >= REGULARITY
            else "excluded"
        )
    )


def seasonal_profile(facts, coverage, years=None):
    """facts[(store,year,month)] = (Decimal amount, observed lines).
    Equal month weight in each store, then equal store weight per cell.
    """
    per_store = defaultdict(lambda: defaultdict(list))
    actual = []
    reasons = defaultdict(set)
    for (store, year, month), method in sorted(coverage.items()):
        if years and year not in years:
            continue
        amount, lines = facts.get((store, year, month), (Decimal(0), 0))
        usable = method != "excluded" and (lines > 0 or method == "confirmed")
        reason = (
            None
            if usable
            else (
                "Недостаточная регулярность записей"
                if method == "excluded"
                else "Нет подтверждённого нуля"
            )
        )
        actual.append(
            dict(
                store=store,
                year=year,
                month=month,
                value=float(amount),
                method=method,
                included=usable,
                reason=reason,
            )
        )
        if usable:
            per_store[store][month].append((year, amount, method))
        else:
            reasons[month].add(reason)
    cells = [
        dict(
            index=None,
            stores=[],
            years=[],
            store_count=0,
            year_count=0,
            estimated=False,
            reason="Нет пригодных данных",
            notes=[],
        )
        for _ in range(12)
    ]
    contributions = defaultdict(list)
    for store, months in per_store.items():
        typical = {
            m: sum((v for y, v, k in values), Decimal(0)) / len(values)
            for m, values in months.items()
        }
        base = sum(typical.values(), Decimal(0)) / len(typical)
        if len(typical) < 2 or base <= 0:
            for m in months:
                reasons[m].add(
                    "Один календарный месяц"
                    if len(typical) < 2
                    else "Нулевая или отрицательная база"
                )
            continue
        for m, value in typical.items():
            observations = months[m]
            contributions[m].append(
                dict(
                    store=store,
                    index=(value / base - 1) * 100,
                    years=[y for y, v, k in observations],
                    estimated=any(k == "estimated" for y, v, k in observations),
                    partial=len(typical) < 12,
                )
            )
    sets = {
        tuple(sorted(v["store"] for v in items)) for items in contributions.values()
    }
    changing = len(sets) > 1
    exact = []
    for m in range(1, 13):
        parts = contributions.get(m, [])
        if not parts:
            cells[m - 1]["reason"] = (
                "; ".join(sorted(reasons[m])) or "Нет пригодных данных"
            )
            exact.append(None)
            continue
        index = sum((p["index"] for p in parts), Decimal(0)) / len(parts)
        ys = sorted({y for p in parts for y in p["years"]})
        notes = []
        if len(ys) == 1:
            notes.append("Данные за один год")
        if any(p["partial"] for p in parts):
            notes.append("База по доступным месяцам")
        if changing:
            notes.append("Меняется состав магазинов")
        cells[m - 1] = dict(
            index=float(index),
            stores=sorted(p["store"] for p in parts),
            years=ys,
            store_count=len(parts),
            year_count=len(ys),
            estimated=changing or any(p["estimated"] for p in parts),
            reason=None,
            notes=notes,
        )
        exact.append(index)
    positives = [v for v in exact if v is not None and v > 0]
    negatives = [v for v in exact if v is not None and v < 0]
    peaks = [i + 1 for i, v in enumerate(exact) if positives and v == max(positives)]
    troughs = [i + 1 for i, v in enumerate(exact) if negatives and v == min(negatives)]
    return dict(
        cells=cells,
        peaks=peaks,
        troughs=troughs,
        actual=actual,
        estimated=changing or any(c["estimated"] for c in cells),
    )
