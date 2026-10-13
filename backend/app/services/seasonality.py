"""Conservative repeatable sales seasonality, never an availability/demand claim."""

from calendar import monthrange
from datetime import date, timedelta
import hashlib
import math

ALGORITHM_VERSION = 3
MIN_YEAR_UNITS = 120
MIN_ACTIVE_MONTHS = 6
MIN_POSITIVE_LINES = 20
HIGH_INDEX = 1.4
MIN_CORRELATION = 0.75
STATUSES = (
    "Сезон сейчас",
    "Скоро сезон",
    "Вне сезона",
    "Сезонность не выявлена",
    "Недостаточно данных",
)
MONTHS = (
    "январе",
    "феврале",
    "марте",
    "апреле",
    "мае",
    "июне",
    "июле",
    "августе",
    "сентябре",
    "октябре",
    "ноябре",
    "декабре",
)


def identity(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def calculate(months, first_year, *, complete_history=True):
    """At most 24 persisted aggregates. Zero observations stay explicitly unknown."""
    values = [[0.0] * 12 for _ in range(2)]
    lines = [0, 0]
    observed_dates = []
    for item in months:
        index = int(item["year"]) - first_year
        if index not in (0, 1) or not 1 <= int(item["month"]) <= 12:
            continue
        values[index][int(item["month"]) - 1] += float(item["units"])
        lines[index] += int(item.get("positive_lines", 0))
        for key in ("first_sale", "last_sale"):
            if item.get(key) and float(item["units"]) > 0:
                value = item[key]
                observed_dates.append(
                    date.fromisoformat(value) if isinstance(value, str) else value
                )
    base = {
        "version": 1,
        "years": [first_year, first_year + 1],
        "pattern": "insufficient",
        "indices": [0.0] * 12,
        "confidence": 0,
        "start_month": None,
        "peak_month": None,
        "end_month": None,
        "season_months": [],
        "windows": [],
        "expected_units": [0.0] * 12,
        "reason": "Недостаточно наблюдений за два полных года; отсутствие продаж не доказывает отсутствие спроса.",
        "availability_known": False,
    }
    totals = [sum(year) for year in values]
    if not complete_history:
        base["reason"] = (
            "В источнике отсутствуют некоторые месяцы двух полных лет; полнота сезонного сравнения не подтверждена."
        )
    elif any(item.get("incomplete_source", False) for item in months):
        base["reason"] = (
            "История содержит продажи из незавершённого или ошибочного импорта; достоверная сезонность не подтверждена."
        )
    elif any(not math.isfinite(x) or x < 0 for year in values for x in year):
        base["reason"] = (
            "Есть некорректные или отрицательные помесячные объёмы; сезонность не оценивается."
        )
    if (
        not complete_history
        or any(item.get("incomplete_source", False) for item in months)
        or any(not math.isfinite(x) or x < 0 for year in values for x in year)
        or min(totals) < MIN_YEAR_UNITS
        or min(lines) < MIN_POSITIVE_LINES
        or any(sum(x > 0 for x in year) < MIN_ACTIVE_MONTHS for year in values)
        or not observed_dates
        or min(observed_dates) > date(first_year, 1, 31)
        or max(observed_dates) < date(first_year + 1, 12, 1)
    ):
        return base
    indices = [[x / (total / 12) for x in year] for year, total in zip(values, totals)]
    merged = [(a + b) / 2 for a, b in zip(*indices)]
    base.update(
        pattern="none",
        indices=merged,
        expected_units=[totals[1] / 12 * x for x in merged],
        reason="Повторяющееся повышение в нескольких соседних месяцах не подтверждено. Исторические остатки неизвестны.",
    )
    variances = [sum((x - 1) ** 2 for x in year) for year in indices]
    if min(variances) < 12 * 0.35**2 or any(
        max(year) / total > 0.5 for year, total in zip(values, totals)
    ):
        return base
    correlation = sum((a - 1) * (b - 1) for a, b in zip(*indices)) / math.sqrt(
        variances[0] * variances[1]
    )
    high = [a >= HIGH_INDEX and b >= HIGH_INDEX for a, b in zip(*indices)]
    starts = [i for i in range(12) if high[i] and not high[(i - 1) % 12]]
    if correlation < MIN_CORRELATION or not starts:
        return base
    windows = []
    for start in starts:
        season = []
        for offset in range(12):
            month = (start + offset) % 12
            if not high[month]:
                break
            season.append(month)
        if 2 <= len(season) <= 8:
            windows.append(
                {
                    "start_month": start + 1,
                    "peak_month": max(season, key=lambda i: merged[i]) + 1,
                    "end_month": season[-1] + 1,
                    "months": [i + 1 for i in season],
                }
            )
    if not windows:
        return base
    season = [i - 1 for window in windows for i in window["months"]]
    primary = max(
        windows,
        key=lambda window: sum(merged[i - 1] for i in window["months"])
        / len(window["months"]),
    )
    peaks = [max(range(12), key=lambda i: year[i]) for year in indices]
    distance = min((peaks[0] - peaks[1]) % 12, (peaks[1] - peaks[0]) % 12)
    if distance > 1 or any(peak not in season for peak in peaks):
        return base
    strength = sum(min(indices[0][i], indices[1][i]) for i in season) / len(season)
    # A reproducibility score, NOT a calibrated probability. No stock history:
    # cap it at 85 and explain the observational limitation in every result.
    confidence = min(
        85, round(50 * max(0, correlation) + 20 + 15 * min(1, (strength - 1) / 1.5))
    )
    if confidence < 70:
        return base
    base.update(
        pattern="confirmed",
        confidence=confidence,
        start_month=primary["start_month"],
        peak_month=primary["peak_month"],
        end_month=primary["end_month"],
        windows=windows,
        season_months=[i + 1 for i in season],
        reason="Повышение повторяется минимум в двух соседних месяцах обоих лет после нормализации годового объёма. Исторические остатки неизвестны.",
    )
    return base


def describe(profile, today):
    result = {
        "status": "Недостаточно данных",
        "index": None,
        "confidence": 0,
        "start_month": None,
        "peak_month": None,
        "end_month": None,
        "priority": 0,
        "action": "Сохранить оценку по действующим критериям",
        "reason": "Готовые актуальные показатели сезонности отсутствуют.",
        "period": "—",
    }
    if not profile:
        return result
    result.update(
        confidence=profile["confidence"],
        index=(
            profile["indices"][today.month - 1]
            if profile["pattern"] != "insufficient"
            else None
        ),
        start_month=profile["start_month"],
        peak_month=profile["peak_month"],
        end_month=profile["end_month"],
        reason=profile["reason"],
    )
    if profile["pattern"] == "insufficient":
        return result
    if profile["pattern"] != "confirmed":
        result["status"] = "Сезонность не выявлена"
        return result
    windows = profile.get("windows") or [
        {
            "start_month": profile["start_month"],
            "peak_month": profile["peak_month"],
            "end_month": profile["end_month"],
            "months": profile["season_months"],
        }
    ]
    active = next(
        (window for window in windows if today.month in window["months"]), None
    )
    upcoming = []
    for window in windows:
        next_start = date(today.year, window["start_month"], 1)
        if next_start < today:
            next_start = date(today.year + 1, window["start_month"], 1)
        upcoming.append((next_start, window))
    next_start, next_window = min(upcoming, key=lambda item: item[0])
    selected = active or next_window
    result.update(
        start_month=selected["start_month"],
        peak_month=selected["peak_month"],
        end_month=selected["end_month"],
    )
    result["status"] = (
        "Сезон сейчас"
        if active
        else "Скоро сезон" if (next_start - today).days <= 45 else "Вне сезона"
    )
    result["period"] = (
        f"{selected['start_month']:02d}–{selected['end_month']:02d}; пик {selected['peak_month']:02d}"
    )
    return result


def recommend(
    row,
    profile,
    criteria,
    today,
    *,
    comparable_scope=True,
    analysis_end=None,
    observed_units=None,
):
    result = describe(profile, today)
    if result["status"] not in ("Сезон сейчас", "Скоро сезон"):
        return result
    start = result["start_month"]
    end = result["end_month"]
    lead = f"Сезонный спрос обычно начинается в {MONTHS[start-1]}, пик — в {MONTHS[result['peak_month']-1]}. "
    stock = row.get("stock")
    if (
        stock is None
        or not math.isfinite(stock)
        or (
            criteria.get("min_stock") is not None
            and row.get("aviatorov_stock") is not None
            and row["aviatorov_stock"] < criteria["min_stock"]
        )
        or stock <= 0
    ):
        result.update(
            action="Проверить наличие; избегать агрессивной скидки",
            reason=lead
            + "Есть стоп-факторы или нет подтверждённого достаточного остатка.",
        )
        return result
    stops = row.get("stop_factors") or []
    if stops:
        if set(stops) == {"Продажи растут без скидки"}:
            result.update(
                priority=40,
                action="Подготовить продвижение без обязательной скидки",
                reason=lead
                + "Продажи растут без скидки; существующий стоп-фактор для акции сохранён.",
            )
        else:
            result.update(
                action="Сохранить ограничения действующих критериев; акция требует проверки",
                reason=lead
                + "Есть стоп-факторы текущего отчёта, поэтому сезонность не повышает приоритет акции.",
            )
        return result
    month = today.month if result["status"] == "Сезон сейчас" else start
    end_of_observations = analysis_end or today
    expected = 0.0
    for offset in range(30):
        observed_day = end_of_observations - timedelta(days=offset)
        if profile["indices"][observed_day.month - 1] is None:
            comparable_scope = False
        expected += (
            profile["expected_units"][observed_day.month - 1]
            / monthrange(observed_day.year, observed_day.month)[1]
        )
    end_year = today.year + (1 if end < today.month else 0)
    season_end = date(end_year, end, monthrange(end_year, end)[1])
    if (
        comparable_scope
        and result["status"] == "Сезон сейчас"
        and (season_end - today).days <= 45
        and "Избыточный запас" in row.get("reasons", [])
        and row.get("status") == "Кандидат"
    ):
        result.update(
            priority=100,
            action="Рассмотреть распродажу остатков",
            reason=lead
            + "Сезон заканчивается в ближайшие 45 дней, подтверждён избыточный запас.",
        )
    elif row.get("status") == "Кандидат" and row.get("reasons"):
        # Before the season starts low pre-season sales are NOT underperformance.
        below = (
            comparable_scope
            and result["status"] == "Сезон сейчас"
            and expected > 0
            and (
                observed_units if observed_units is not None else row.get("units_30", 0)
            )
            < expected * 0.8
        )
        result.update(
            priority=95 if below else 80,
            action=(
                "Рассмотреть акцию после проверки причин низких продаж"
                if below
                else "Подготовить продвижение; скидка требует отдельной оценки"
            ),
            reason=lead
            + (
                "Продажи ниже 80% исторического ориентира за эти 30 календарных дней; есть действующие основания для акции."
                if below
                else "Есть действующие основания для продвижения и достаточный остаток."
            ),
        )
    else:
        result.update(
            priority=40,
            action="Подготовить продвижение без обязательной скидки",
            reason=lead
            + "Приближается или продолжается сезонный спрос; оснований для скидки по действующим критериям нет.",
        )
    return result


def _windows(high, indices):
    windows = []
    for start in range(12):
        if not high[start] or high[(start - 1) % 12]:
            continue
        months = []
        for offset in range(12):
            month = (start + offset) % 12
            if not high[month]:
                break
            months.append(month)
        if 2 <= len(months) <= 8:
            windows.append(
                {
                    "start_month": start + 1,
                    "peak_month": max(months, key=lambda i: indices[i] or 0) + 1,
                    "end_month": months[-1] + 1,
                    "months": [i + 1 for i in months],
                }
            )
    return windows


def calculate_history(months, today):
    """All closed observations, age-weighted evidence, no fixed year boundary.

    State is O(years + 12 + distinct seasonal masks), not O(year pairs).
    Missing availability never becomes an assertion of zero demand.
    """
    from itertools import combinations
    from statistics import median

    base = {
        "pattern": "insufficient",
        "confidence": 0,
        "start_month": None,
        "peak_month": None,
        "end_month": None,
        "season_months": [],
        "windows": [],
        "historical_windows": [],
        "expected_units": [0.0] * 12,
        "availability_known": False,
    }
    base.update(
        version=ALGORITHM_VERSION,
        history_scope="all",
        years=[],
        compared_pairs=0,
        observed_months=[],
        indices=[None] * 12,
        historical_indices=[None] * 12,
        annual=[],
        trend_percent=None,
        stability=0,
        effective_seasons=0,
        half_life_years=None,
    )
    base["reason"] = (
        "Недостаточно повторных наблюдений: нужны два сопоставимых сезона с шестью общими завершёнными месяцами, 120 единицами и 20 положительными строками продаж. Исторические остатки неизвестны."
    )
    annual = {}
    for item in months:
        year, month = int(item["year"]), int(item["month"])
        if not 1 <= month <= 12 or (year, month) >= (today.year, today.month):
            continue
        units = float(item["units"])
        group = annual.setdefault(
            year, {"values": [0.0] * 12, "lines": 0, "active": set(), "bad": False}
        )
        group["values"][month - 1] += units
        group["lines"] += int(item.get("positive_lines", 0))
        group["bad"] |= (
            bool(item.get("incomplete_source")) or not math.isfinite(units) or units < 0
        )
        if units > 0 and math.isfinite(units):
            group["active"].add(month - 1)
    base["years"] = sorted(annual)
    eligible = {
        year: group
        for year, group in annual.items()
        if not group["bad"]
        and sum(group["values"]) >= MIN_YEAR_UNITS
        and group["lines"] >= MIN_POSITIVE_LINES
        and len(group["active"]) >= 4
    }
    ordered = sorted(eligible)
    # Half the evidence after two typical observed season intervals; every old
    # season keeps a positive weight. Sparse histories get a longer half-life.
    half_life = (
        2 * median([b - a for a, b in zip(ordered, ordered[1:])])
        if len(ordered) > 1
        else 2
    )
    base["half_life_years"] = half_life
    weights = {
        year: max(1e-12, 2 ** (-(today.year - year - 1) / half_life))
        for year in ordered
    }
    numerator, denominator = [0.0] * 12, [0.0] * 12
    historic, historic_count = [0.0] * 12, [0] * 12
    votes = [0.0] * 12
    historical_votes = [0] * 12
    support_masks = {}  # bounded by 2**12, never keep every pair
    corr_sum = valid_weight = total_weight = 0.0
    slopes = []
    adjacent = set(zip(ordered, ordered[1:]))
    participating = set()
    for (year_a, a), (year_b, b) in combinations(sorted(eligible.items()), 2):
        common = list(
            range(
                max(min(a["active"]), min(b["active"])),
                min(max(a["active"]), max(b["active"])) + 1,
            )
        )
        if len(common) < 6:
            continue
        totals = [sum(g["values"][i] for i in common) for g in (a, b)]
        if min(totals) <= 0:
            continue
        participating.update((year_a, year_b))
        base["compared_pairs"] += 1
        pair_weight = weights[year_a] * weights[year_b]
        total_weight += pair_weight
        normalized = [
            [g["values"][i] / (total / len(common)) for i in common]
            for g, total in zip((a, b), totals)
        ]
        va, vb = [sum((x - 1) ** 2 for x in profile) for profile in normalized]
        corr = (
            sum((x - 1) * (y - 1) for x, y in zip(*normalized)) / math.sqrt(va * vb)
            if va * vb
            else 0
        )
        valid = (
            min(va, vb) >= len(common) * 0.35**2
            and corr >= MIN_CORRELATION
            and all(max(profile) / sum(profile) <= 0.5 for profile in normalized)
        )
        if valid:
            corr_sum += pair_weight * corr
            valid_weight += pair_weight
        mask = 0
        for position, month in enumerate(common):
            value = (normalized[0][position] + normalized[1][position]) / 2
            numerator[month] += value * pair_weight
            denominator[month] += pair_weight
            historic[month] += value
            historic_count[month] += 1
            if (
                valid
                and min(normalized[0][position], normalized[1][position]) >= HIGH_INDEX
            ):
                votes[month] += pair_weight
                historical_votes[month] += 1
                mask |= 1 << month
        if mask:
            support_masks[mask] = support_masks.get(mask, 0) + pair_weight
        # Trend: only adjacent observed years, same calendar-month exposure.
        if (year_a, year_b) in adjacent:
            slopes.append(math.log(totals[1] / totals[0]) / (year_b - year_a))
    for year, group in sorted(annual.items()):
        active = group["active"]
        indices = [None] * 12
        if active and not group["bad"]:
            first, last = min(active), max(active)
            mean = sum(group["values"]) / (last - first + 1)
            if mean:
                for i in range(first, last + 1):
                    indices[i] = group["values"][i] / mean
        windows = _windows(
            [value is not None and value >= HIGH_INDEX for value in indices], indices
        )
        base["annual"].append(
            {
                "year": year,
                "eligible": year in eligible,
                "observed_months": [
                    i + 1 for i, v in enumerate(indices) if v is not None
                ],
                "total_units": sum(group["values"]) if not group["bad"] else None,
                "peak_month": (
                    max(active, key=lambda i: group["values"][i]) + 1
                    if active and not group["bad"]
                    else None
                ),
                "windows": windows,
                "weight": weights.get(year, 0),
            }
        )
    if not base["compared_pairs"]:
        return base
    indices = [
        numerator[i] / denominator[i] if denominator[i] else None for i in range(12)
    ]
    base.update(
        pattern="none",
        indices=indices,
        historical_indices=[
            historic[i] / historic_count[i] if historic_count[i] else None
            for i in range(12)
        ],
        observed_months=[i + 1 for i, w in enumerate(denominator) if w],
        trend_percent=100 * math.expm1(median(slopes)) if slopes else None,
        stability=round(100 * valid_weight / total_weight) if total_weight else 0,
        effective_seasons=sum(weights[y] for y in participating) ** 2
        / sum(weights[y] ** 2 for y in participating),
    )
    base["reason"] = (
        "Повторяемые сезонные окна не подтверждены с учётом всей истории и давности сезонов. Отсутствие продаж не доказывает отсутствие спроса."
    )
    base["historical_windows"] = _windows(
        [
            bool(historic_count[i]) and historical_votes[i] / historic_count[i] >= 2 / 3
            for i in range(12)
        ],
        base["historical_indices"],
    )
    high = [
        bool(denominator[i]) and votes[i] / denominator[i] >= 2 / 3 for i in range(12)
    ]
    windows = []
    for window in _windows(high, indices):
        mask = sum(1 << (m - 1) for m in window["months"])
        # A single pair must support all months; never combine unrelated peaks.
        if any(supported & mask == mask for supported in support_masks):
            windows.append(window)
    if not windows:
        return base
    latest_year = max(participating)
    latest = eligible[latest_year]
    partial = any(len(g["active"]) < 12 for g in eligible.values()) or any(
        g["bad"] for g in annual.values()
    )
    gaps = 0
    for group in eligible.values():
        run = 0
        for i in range(min(group["active"]), max(group["active"]) + 1):
            run = run + 1 if group["values"][i] == 0 else 0
            gaps = max(gaps, run)
    age = max(0, today.year - latest_year - 1)
    confidence = min(
        75 if partial else 85,
        round(
            50 * (corr_sum / valid_weight)
            + 20
            + 10 * min(1, (base["effective_seasons"] - 1) / 2)
        ),
    )
    confidence = max(0, confidence - min(20, gaps * 3) - min(35, age * 5))
    if confidence < 60:
        base.update(
            pattern="insufficient",
            confidence=confidence,
            reason="Исторический сезон обнаружен, но свежести, полноты или непрерывности наблюдений недостаточно для актуального вывода. Исторические остатки неизвестны.",
        )
        return base
    primary = max(
        windows,
        key=lambda w: sum(indices[i - 1] for i in w["months"]) / len(w["months"]),
    )
    # Calibrate historical month quantities to the latest comparable year's
    # COMMON exposure. A summer-only year must not lower a winter forecast
    # merely because the winter peak has not happened yet.
    expected_sum, expected_weight = [0.0] * 12, [0.0] * 12
    for year in participating:
        group = eligible[year]
        first = max(min(group["active"]), min(latest["active"]))
        last = min(max(group["active"]), max(latest["active"]))
        common = list(range(first, last + 1))
        total = sum(group["values"][i] for i in common)
        if len(common) < 6 or total <= 0:
            continue
        scale = sum(latest["values"][i] for i in common) / total
        for month in range(min(group["active"]), max(group["active"]) + 1):
            expected_sum[month] += group["values"][month] * scale * weights[year]
            expected_weight[month] += weights[year]
    expected = [
        expected_sum[i] / expected_weight[i] if expected_weight[i] else 0
        for i in range(12)
    ]
    base.update(
        pattern="confirmed",
        confidence=confidence,
        windows=windows,
        season_months=sorted({i for window in windows for i in window["months"]}),
        start_month=primary["start_month"],
        peak_month=primary["peak_month"],
        end_month=primary["end_month"],
        expected_units=expected,
    )
    base["reason"] = (
        f"Сезон подтверждён по всей истории: {len(eligible)} сопоставимых лет, {base['compared_pairs']} сравнений; последние сезоны имеют больший вес. Уверенность учитывает давность и разрывы продаж. Исторические остатки неизвестны."
    )
    return base
