"""Local Chromium and fake APIs only; screenshots contain synthetic data."""

import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

out = Path("docs/screenshots/category-seasonality")
out.mkdir(parents=True, exist_ok=True)


def profile(peak):
    v = [275 if m == peak else -25 for m in range(12)]
    return dict(
        indices=v,
        years=[2023, 2024, 2025],
        annual=[
            dict(
                year=y,
                values=[50 if m == peak else 10 for m in range(12)],
                indices=v,
                included=True,
                reason=None,
                coverage=[True] * 12,
            )
            for y in [2023, 2024, 2025]
        ],
        peaks=[dict(month=peak + 1, count=3, total=3, repeating=True)],
        message=None,
    )


rows = [
    dict(
        key="april",
        parent_key="parent1",
        parent="Посуда",
        name="Чашки",
        units=profile(3),
        revenue=profile(3),
    ),
    dict(
        key="july",
        parent_key="parent1",
        parent="Посуда",
        name="Тарелки",
        units=profile(6),
        revenue=profile(6),
    ),
]
nodes = [
    dict(
        id="parent1",
        name="Посуда",
        children=[
            dict(id=r["key"], name=r["name"], value=r["key"], children=[]) for r in rows
        ],
    )
]
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
        if path == "reports/category-seasonality/options":
            data = dict(
                years=[2025, 2024, 2023],
                nodes=nodes,
                brand=["Бренд A", "Бренд B"],
                manufacturer=["Завод A", "Завод B"],
            )
        elif path == "filters":
            data = dict(departments=["Магазин А", "Магазин Б"])
        elif path == "reports/category-seasonality":
            payload = json.loads(r.request.post_data)
            requests.append(payload)
            data = dict(
                rows=rows,
                filters=payload,
                years=payload["years"],
                coverage={},
                warning="Синтетический стенд. Покрытие подтверждено успешными файлами с объявленным периодом.",
            )
        r.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(data, ensure_ascii=False),
        )

    page.route("**/*", route)
    page.goto("http://127.0.0.1:18528/vr/sales/reports/category-seasonality")
    page.wait_for_timeout(1000)
    if errors:
        raise AssertionError(errors)
    expect(page.get_by_role("heading", name="Сформируйте отчёт")).to_be_visible()
    assert not requests
    page.get_by_role("button", name="Категории и подкатегории", exact=True).click()
    page.get_by_role("checkbox", name="Посуда", exact=True).check()
    page.get_by_role("button", name="Развернуть: Посуда").click()
    page.get_by_role("checkbox", name="Чашки", exact=True).uncheck()
    page.get_by_role("checkbox", name="Чашки", exact=True).check()
    page.keyboard.press("Escape")
    assert not requests
    page.get_by_role("button", name="Сформировать отчёт", exact=True).click()
    expect(page.get_by_role("heading", name="Тепловая карта спроса")).to_be_visible()
    assert len(requests) == 1 and set(requests[0]["categories"]) == {"april", "july"}
    expect(page.get_by_text("Апр · 3 из 3 лет · повторяется")).to_be_visible()
    expect(page.get_by_text("Июл · 3 из 3 лет · повторяется")).to_be_visible()
    page.screenshot(path=str(out / "heatmap-desktop.png"), full_page=True)
    page.get_by_role("button", name="Чашки", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page.get_by_role("heading", name="Чашки", exact=True)).to_be_visible()
    expect(page.locator("dialog .recharts-surface").first).to_be_visible()
    page.screenshot(path=str(out / "detail-desktop.png"), full_page=True)
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.get_by_role("button", name="Чашки", exact=True)).to_be_focused()
    page.get_by_role("button", name="Тарелки", exact=True).click()
    expect(page.get_by_role("heading", name="Тарелки", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Выручка", exact=True).click()
    assert len(requests) == 1
    page.locator(".report-filters > label").filter(has_text="Магазин").locator(
        "button.multi-trigger"
    ).click()
    page.get_by_role("checkbox", name="Магазин А", exact=True).check()
    page.locator(".report-filters > label").filter(has_text="Магазин").locator(
        "button.multi-trigger"
    ).click()
    expect(
        page.get_by_role("status").filter(has_text="Параметры изменены")
    ).to_be_visible()
    assert len(requests) == 1
    page.set_viewport_size(dict(width=390, height=844))
    page.wait_for_function(
        "document.querySelector('.app>aside').getBoundingClientRect().right <= 1"
    )
    page.screenshot(path=str(out / "heatmap-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
    page.locator(".category-season-scroll").first.evaluate("(e)=>e.scrollLeft=800")
    name = page.get_by_role("button", name="Чашки", exact=True)
    expect(name).to_be_visible()
    name.click()
    page.screenshot(path=str(out / "detail-mobile.png"), full_page=True)
    page.keyboard.press("Escape")
    scroller = page.locator(".category-season-scroll").first
    scroller.evaluate(
        """e=>{const body=e.querySelector('tbody');const row=body.querySelector('tr:last-child');for(let i=0;i<25;i++)body.appendChild(row.cloneNode(true));e.scrollLeft=0;e.scrollTop=0}"""
    )
    header = page.locator(".category-season-heatmap thead th").first
    scroller.evaluate("e=>{e.scrollTop=300;e.scrollLeft=600}")
    y = header.bounding_box()["y"]
    scroller.evaluate("e=>e.scrollTop=450")
    assert abs(header.bounding_box()["y"] - y) < 2
    assert abs(y - scroller.bounding_box()["y"]) < 2
    assert abs(header.bounding_box()["x"] - scroller.bounding_box()["x"]) < 2
    assert len(requests) == 1 and not errors, errors
    print(
        "PASS: April/July, parent selection, keyboard/focus, cached detail/metric, draft snapshot, mobile scroll and charts"
    )
    browser.close()
