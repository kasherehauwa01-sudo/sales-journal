"""Real Chromium; fake published snapshots, never raw reports or external APIs."""

import json, sys
from pathlib import Path
from decimal import Decimal
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from app.services.category_seasonality import seasonal_profile

out = Path("docs/screenshots/category-seasonality")
out.mkdir(parents=True, exist_ok=True)


def profile(peak, trough):
    coverage = {
        ("Store", y, m): "estimated" if m != 2 else "excluded"
        for y in [2024, 2025, 2026]
        for m in range(1, 13)
        if not (y == 2024 and m < 9) and not (y == 2026 and m > 10)
    }
    facts = {
        k: (Decimal(50 if k[2] == peak else 1 if k[2] == trough else 10), 1)
        for k in coverage
    }
    return seasonal_profile(facts, coverage)


rows = [
    dict(
        key='["category:1","Чашки"]',
        parent_key="category:1",
        parent="Посуда",
        name="Чашки",
        units=profile(4, 1),
        revenue=profile(4, 1),
    ),
    dict(
        key='["category:1","Тарелки"]',
        parent_key="category:1",
        parent="Посуда",
        name="Тарелки",
        units=profile(7, 3),
        revenue=profile(7, 3),
    ),
]
options = dict(
    years=[2026, 2025, 2024],
    stores=["Store"],
    nodes=[
        dict(
            id="category:1",
            name="Посуда",
            children=[
                dict(id=r["key"], name=r["name"], value=r["key"], children=[])
                for r in rows
            ],
        )
    ],
    brand=["A", "B"],
    manufacturer=["X", "Y"],
    managers=["M"],
    buyer_types=["Опт"],
)
requests = []
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium",
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = browser.new_page(viewport=dict(width=1440, height=1050), locale="ru-RU")
    page.on("pageerror", lambda e: errors.append(str(e)))

    def route(r):
        parsed = urlsplit(r.request.url)
        if parsed.hostname not in ["localhost", "127.0.0.1"]:
            return r.abort()
        if not parsed.path.startswith("/vr/sales/api/"):
            return r.continue_()
        path = parsed.path.split("/api/")[1]
        data = []
        if path.startswith("reports/category-seasonality"):
            payload = json.loads(r.request.post_data)
            requests.append((path, payload))
            chosen = [
                r
                for r in rows
                if not payload["categories"]
                or r["key"] in payload["categories"]
                or r["parent_key"] in payload["categories"]
            ]
            copied = json.loads(json.dumps(chosen))
            if not path.endswith("/detail"):
                for row in copied:
                    for metric in ["units", "revenue"]:
                        row[metric]["actual"] = []
            data = dict(
                state="ready",
                snapshot_id="snapshot-fixture",
                rows=copied,
                options=options,
                filters=payload,
                last_success="2026-11-13T12:00:00Z",
                through="2026-10-31",
                next_update="2026-11-14T16:00:00Z",
                updating=False,
                previous_after_error=False,
                warning="Индекс — отклонение от собственного среднего. Ориентировочное покрытие ≥90% дней не гарантирует полноту 1С.",
            )
        r.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(data, ensure_ascii=False),
        )

    page.route("**/*", route)
    page.goto("http://127.0.0.1:18528/vr/sales/reports/category-seasonality")
    expect(page.get_by_role("heading", name="Тепловая карта спроса")).to_be_visible()
    assert len(requests) == 1
    expect(page.get_by_role("button", name="Сформировать отчёт")).to_have_count(0)
    expect(page.get_by_text("Пик спроса", exact=True).first).to_be_visible()
    expect(page.get_by_text("Максимальный спад", exact=True).first).to_be_visible()
    expect(page.locator("td.no-data").first).to_be_visible()
    page.get_by_role("button", name="Категории и подкатегории", exact=True).click()
    page.get_by_role("checkbox", name="Посуда", exact=True).check()
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    expect(
        page.get_by_role("status").filter(has_text="Загрузка готового")
    ).to_have_count(0)
    assert set(requests[-1][1]["categories"]) == {r["key"] for r in rows}
    page.screenshot(path=str(out / "heatmap-desktop.png"), full_page=True)
    page.get_by_role("button", name="Чашки", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page.get_by_text("Загрузка детализации из снимка…")).to_have_count(0)
    expect(page.get_by_role("heading", name="Чашки", exact=True)).to_be_visible()
    expect(page.locator("dialog .recharts-surface").first).to_be_visible()
    assert (
        requests[-1][0].endswith("/detail")
        and requests[-1][1]["snapshot_id"] == "snapshot-fixture"
    )
    page.screenshot(path=str(out / "detail-desktop.png"), full_page=True)
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Чашки", exact=True)).to_be_focused()
    before = len(requests)
    page.get_by_role("button", name="Выручка", exact=True).click()
    assert len(requests) == before
    page.set_viewport_size(dict(width=390, height=844))
    page.wait_for_function(
        "document.querySelector('.app>aside').getBoundingClientRect().right<=1"
    )
    page.screenshot(path=str(out / "heatmap-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
    scroller = page.locator(".category-season-scroll").first
    scroller.evaluate("(e)=>e.scrollLeft=700")
    page.get_by_role("button", name="Тарелки", exact=True).click()
    expect(page.get_by_role("heading", name="Тарелки", exact=True)).to_be_visible()
    page.screenshot(path=str(out / "detail-mobile.png"), full_page=True)
    assert not errors, errors
    assert all(
        path in ["reports/category-seasonality", "reports/category-seasonality/detail"]
        for path, _ in requests
    )
    print(
        "PASS: automatic snapshot, separate April/July, parent filter, peak/trough/no data, keyboard/modal/focus, no raw rebuild, mobile scrolling"
    )
    browser.close()
