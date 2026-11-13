"""Local Chromium: retired report absent; promotion selection/photos/stocks remain."""

import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

out = Path("/workspace/output/seasonality-removal-ui")
out.mkdir(parents=True, exist_ok=True)
requests = []
errors = []
item = dict(
    key="code:001",
    name="Тестовый кандидат",
    code="001",
    article="A",
    image_url=None,
    catalog_manager="Менеджер тест",
    category="Чашки",
    brand="Бренд",
    manufacturer="Завод",
    reasons=["Избыточный запас"],
    stop_factors=[],
    status="Кандидат",
    revenue=100,
    units=2,
    checks=1,
    average_price=50,
    units_30=1,
    units_previous_30=2,
    units_60_90=3,
    stock=500,
    aviatorov_stock=250,
    stock_months=20,
    stock_value=25000,
    days_without_sales=50,
    monthly_units=1,
)
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium",
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="ru-RU")
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(
        "localStorage.setItem('promotion-candidates-columns-v2',JSON.stringify(['photo','name','season','priority','aviatorov','manager']))"
    )

    def route(r):
        parsed = urlsplit(r.request.url)
        if parsed.hostname not in ("localhost", "127.0.0.1"):
            return r.abort()
        if not parsed.path.startswith("/vr/sales/api/"):
            return r.continue_()
        path = parsed.path.split("/api/")[1]
        requests.append(path)
        data = []
        if path == "filters":
            data = {"departments": []}
        elif path == "reports/promotion-candidates":
            assert "seasonality" not in (r.request.post_data or "")
            data = {
                "items": [item],
                "total": 1,
                "table_limit": 300,
                "summary": {"candidates": 1, "stale": 0, "excess": 1, "decline": 0},
                "catalog_available": True,
                "limitations": [],
            }
        elif path.endswith("/product-catalog"):
            data = {
                "stocks": [{"name": "Авиаторов Зал+Склад", "quantity": 250}],
                "manager": "Менеджер тест",
            }
        elif path == "settings/auth/session":
            data = {}
        r.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    page.route("**/*", route)
    page.goto("http://127.0.0.1:18528/vr/sales/reports/promotion-candidates")
    expect(page.locator('a[href$="/reports/seasonality"]')).to_have_count(0)
    expect(page.get_by_role("checkbox", name="Учитывать сезонность")).to_have_count(0)
    assert "reports/promotion-candidates" not in requests
    page.get_by_role("button", name="Сформировать отчёт").click()
    expect(page.get_by_text("Тестовый кандидат", exact=True)).to_be_visible()
    expect(
        page.get_by_role("columnheader", name="Сезонность", exact=True)
    ).to_have_count(0)
    expect(
        page.get_by_role("columnheader", name="Сезонный приоритет", exact=True)
    ).to_have_count(0)
    expect(
        page.get_by_role(
            "columnheader",
            name="Остатки Авиаторов Зал+Склад Описание: Остатки Авиаторов Зал+Склад",
            exact=True,
        )
    ).to_be_visible()
    expect(
        page.get_by_role("columnheader", name="Менеджер Описание: Менеджер", exact=True)
    ).to_be_visible()
    page.screenshot(path=str(out / "promotion-without-seasonality.png"), full_page=True)
    page.get_by_text("Тестовый кандидат", exact=True).click()
    expect(page.get_by_role("heading", name="Сезонность и рекомендация")).to_have_count(
        0
    )
    expect(page.get_by_role("heading", name="Динамика количества")).to_be_visible()
    expect(page.get_by_text("Нет фото").first).to_be_visible()
    page.screenshot(
        path=str(out / "product-card-without-seasonality.png"), full_page=True
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(out / "product-card-mobile.png"), full_page=True)
    assert not errors, errors
    assert not any("/seasonality" in r for r in requests)
    print(
        "PASS: no seasonality navigation/controls/columns/card/API calls; legacy column preferences, product card/photo placeholder/warehouse/manager preserved; desktop/mobile without JS errors"
    )
    browser.close()
