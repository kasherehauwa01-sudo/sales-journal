"""Детерминированные правила RFM, не зависящие от конкретной БД."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


SEGMENTS = (
    "Постоянные активные", "Крупные клиенты", "Новые", "Перспективные",
    "Засыпающие", "Потерянные", "Редко покупающие", "Неактивные",
)


def tied_quintiles(values: list[float], *, high_is_good: bool = True) -> tuple[list[int], list[dict]]:
    """Присваивает 1–5 по рангам уникальных значений, не разрывая равные значения.

    Граница строится по накопленной позиции значения в отсортированной выборке.
    Поэтому группы близки к квинтилям, но все одинаковые значения всегда получают
    одну оценку. Для малой/вырожденной выборки шкала естественно сжимается.
    """
    if not values:
        return [], []
    ordered = sorted(set(values))
    counts = {value: values.count(value) for value in ordered}
    scores: dict[float, int] = {}
    passed = 0
    for value in ordered:
        midpoint = passed + counts[value] / 2
        score = min(5, max(1, int(midpoint * 5 / len(values)) + 1))
        if not high_is_good:
            score = 6 - score
        scores[value] = score
        passed += counts[value]
    result = [scores[value] for value in values]
    boundaries = [
        {"score": score, "min": min(v for v, s in scores.items() if s == score),
         "max": max(v for v, s in scores.items() if s == score)}
        for score in sorted(set(scores.values()), reverse=True)
    ]
    return result, boundaries


@dataclass(frozen=True)
class SegmentSettings:
    new_days: int = 30
    lost_days: int = 180
    sleeping_cycle: float = 1.5
    lost_cycle: float = 3.0


def segment_client(row: dict, settings: SegmentSettings) -> tuple[str, list[str]]:
    """Выбирает основной сегмент по явному приоритету риска → роста → ценности."""
    r, f, m = row["r_score"], row["f_score"], row["m_score"]
    recency, frequency = row["recency_days"], row["frequency"]
    overdue = row.get("cycle_overdue")
    tags: list[str] = []
    if m >= 4: tags.append("Крупный клиент")
    if f >= 4: tags.append("Высокая частота покупок")
    if r >= 4: tags.append("Недавняя покупка")

    is_new = row["first_purchase_days"] <= settings.new_days and frequency <= 2
    is_lost = recency >= settings.lost_days or (overdue is not None and overdue >= settings.lost_cycle)
    is_sleeping = not is_lost and ((overdue is not None and overdue >= settings.sleeping_cycle) or r <= 2) and (f >= 3 or m >= 3)
    if is_lost: segment = "Неактивные" if f <= 2 and m <= 2 else "Потерянные"
    elif is_sleeping: segment = "Засыпающие"
    elif is_new: segment = "Новые"
    elif r >= 4 and f >= 4: segment = "Постоянные активные"
    elif r >= 4 and frequency >= 2: segment = "Перспективные"
    elif m >= 5: segment = "Крупные клиенты"
    elif f <= 2: segment = "Редко покупающие"
    else: segment = "Неактивные"
    return segment, tags


def enrich(rows: list[dict], end: date, settings: SegmentSettings) -> tuple[list[dict], dict]:
    """Добавляет score, сегменты и опубликованные фактические границы."""
    rs, rb = tied_quintiles([r["recency_days"] for r in rows], high_is_good=False)
    fs, fb = tied_quintiles([r["frequency"] for r in rows])
    ms, mb = tied_quintiles([r["monetary"] for r in rows])
    for row, r_score, f_score, m_score in zip(rows, rs, fs, ms):
        row.update(r_score=r_score, f_score=f_score, m_score=m_score,
                   rfm_code=f"{r_score}{f_score}{m_score}")
        row["first_purchase_days"] = (end - row["first_purchase"]).days
        row["segment"], row["tags"] = segment_client(row, settings)
    return rows, {"r": rb, "f": fb, "m": mb}
