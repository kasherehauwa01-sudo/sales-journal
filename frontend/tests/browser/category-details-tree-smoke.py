"""Chromium with fake API only: loading, cache, tree, mobile and no auto reports."""

import json, os
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

out = Path(os.environ.get("CATEGORY_UI_ARTIFACTS", "/workspace/output/category-ui"))
out.mkdir(parents=True, exist_ok=True)
metrics = {
    k: dict(current=200, previous=100, difference=100, change_percent=100)
    for k in ["revenue", "units", "checks", "average_check", "categories"]
}


def row(name):
    return dict(
        category=name,
        current_revenue=100,
        previous_revenue=50,
        current_units=2,
        current_checks=1,
        current_average_check=100,
        current_share=50,
        previous_share=50,
        share_change_pp=0,
        revenue_change=50,
        revenue_change_percent=100,
        contribution_percent=50,
        contribution="Рост",
    )


rows = [row("Чашки"), row("Тарелки")]
period = dict(start="2026-09-01", end="2026-09-30")
report = dict(
    period=period,
    previous_period=period,
    metrics=metrics,
    categories=rows,
    category_options=["Чашки", "Тарелки"],
    structure=[],
    growth_drivers=[],
    decline_drivers=[],
    year_comparison=dict(
        period=period,
        metrics=metrics,
        categories=rows,
        growth_drivers=[],
        decline_drivers=[],
    ),
)
nodes = [
    dict(
        id="p",
        name="Посуда",
        children=[
            dict(id=x, name=n, value=n, section=n, children=[])
            for x, n in [("a", "Чашки"), ("b", "Тарелки")]
        ],
    )
]
pending = []
requests = []
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium",
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = browser.new_page(viewport=dict(width=1440, height=1000), locale="ru-RU")
    page.on("pageerror", lambda e: errors.append(str(e)))

    def route(r):
        u = urlsplit(r.request.url)
        if u.hostname not in ("127.0.0.1", "localhost"):
            return r.abort()
        if not u.path.startswith("/vr/sales/api/"):
            return r.continue_()
        path = u.path.split("/api/", 1)[1]
        requests.append(path)
        if path.endswith("/details"):
            pending.append(r)
            return
        data = []
        if path == "filters":
            data = {"departments": []}
        elif path == "settings/auth/session":
            data = {}
        elif path == "reports/product-sales/catalog/category-tree":
            data = {"nodes": nodes}
        elif path == "reports/category-analytics":
            data = report
        r.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    page.route("**/*", route)
    page.goto("http://127.0.0.1:18528/vr/sales/reports/category-analytics")
    expect(page.get_by_role("button", name="Сформировать отчёт")).to_be_visible()
    assert not any(
        x.startswith("reports/category-analytics?") or x == "reports/category-analytics"
        for x in requests
    )
    page.get_by_role("button", name="Сформировать отчёт").click()
    target = page.locator(".category-table tbody tr").filter(has_text="Чашки")
    target.click()
    dialog = page.get_by_role("dialog", name="Статистика категории: Чашки")
    expect(dialog.get_by_text("Загружаем статистику категории…")).to_be_visible()
    expect(dialog.get_by_text("Выручка", exact=True)).to_be_visible()
    expect(dialog).to_have_attribute("aria-busy", "true")
    assert len(pending) == 1
    page.screenshot(path=str(out / "category-loading-desktop.png"), full_page=True)
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            dict(
                category=rows[0],
                products=[
                    dict(
                        name="Чашка тестовая",
                        code="001",
                        article="A",
                        revenue=100,
                        units=2,
                        checks=1,
                        share=100,
                        revenue_change=50,
                    )
                ],
                group_by="day",
                points=[dict(period="2026-09-01", revenue=100, units=2)],
            )
        ),
    )
    expect(dialog.get_by_text("Чашка тестовая")).to_be_visible()
    page.screenshot(path=str(out / "category-ready-desktop.png"), full_page=True)
    dialog.get_by_role("button", name="Закрыть").click()
    target.click()
    expect(page.get_by_role("dialog").get_by_text("Чашка тестовая")).to_be_visible()
    assert len(pending) == 1
    page.get_by_role("dialog").get_by_role("button", name="Закрыть").click()
    page.get_by_role("button", name="Категория", exact=True).click()
    page.get_by_role("button", name="Развернуть: Посуда").click()
    page.get_by_role("checkbox", name="Чашки", exact=True).check()
    assert page.get_by_role("checkbox", name="Посуда").evaluate(
        "(el)=>el.indeterminate"
    )
    page.get_by_role("textbox", name="Поиск: Категория").fill("тарелки")
    expect(page.get_by_role("checkbox", name="Тарелки")).to_be_visible()
    page.get_by_role("checkbox", name="Посуда").check()
    assert sum(x == "reports/category-analytics" for x in requests) == 1
    page.screenshot(path=str(out / "category-tree-desktop.png"), full_page=True)
    page.set_viewport_size(dict(width=390, height=844))
    page.get_by_role("textbox", name="Поиск: Категория").fill("")
    box = page.locator(".category-tree-filter .catalog-dropdown").bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= 391, box
    page.screenshot(path=str(out / "category-tree-mobile.png"), full_page=True)
    page.get_by_role("textbox", name="Поиск: Категория").press("Escape")
    for report_path, label in [
        ("promotion-candidates", "Категория"),
        ("product-analytics", "Подкатегория"),
    ]:
        page.goto("http://127.0.0.1:18528/vr/sales/reports/" + report_path)
        page.get_by_role("button", name=label, exact=True).click()
        expect(page.get_by_role("checkbox", name="Посуда")).to_be_visible()
        box = page.locator(".category-tree-filter .catalog-dropdown").bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 391, (
            report_path,
            box,
        )
        page.screenshot(
            path=str(out / (report_path + "-tree-mobile.png")), full_page=True
        )
    assert not errors, errors
    print(
        "PASS: immediate KPI/loading, completed detail/cache, catalog tree/search/partial selection, no automatic reports, mobile bounds, no JS errors"
    )
    browser.close()
