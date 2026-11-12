"""Local mocked UI only: never contacts backend/production/FTP/SMTP."""

import json, os
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright

url = os.environ.get("SALES_CONTROL_UI_URL", "http://127.0.0.1:18528/vr/sales/imports")
assert urlsplit(url).hostname in ("127.0.0.1", "localhost")
out = Path(os.environ.get("SALES_CONTROL_ARTIFACTS", "/tmp/sales-control-ui"))
out.mkdir(parents=True, exist_ok=True)
rows = [
    dict(
        department="Тестовый магазин Север",
        sales_date="2026-11-11",
        status="Продажи отсутствуют",
    ),
    dict(
        department="Тестовый магазин Центр",
        sales_date="2026-11-10",
        status="Продажи отсутствуют",
    ),
]
hidden = set()
writes = []
requests = []
errors = []
cfg = dict(enabled=False, send_time="09:00", recipients=[], revision=1)
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium",
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="ru-RU")
    page.on("pageerror", lambda e: errors.append(str(e)))

    def route(r):
        request = r.request
        parsed = urlsplit(request.url)
        if parsed.hostname not in ("127.0.0.1", "localhost"):
            return r.abort()
        if not parsed.path.startswith("/vr/sales/api/"):
            return r.continue_()
        path = parsed.path.split("/api/")[1]
        requests.append(path)
        value = []
        if request.method != "GET":
            writes.append((request.method, path))
        if path == "settings/auth/session":
            value = {}
        elif path == "sales-control/notification-settings":
            if request.method == "PUT":
                cfg.update(request.post_data_json)
                cfg["revision"] += 1
            value = cfg
        elif path == "sales-control/notification-status":
            value = None
        elif path == "sales-control/departments":
            value = [dict(department=x["department"]) for x in rows]
        elif path == "sales-control/exclusions":
            for x in request.post_data_json["items"]:
                key = (x["department"], x["sales_date"])
                if request.method == "POST":
                    hidden.add(key)
                else:
                    hidden.discard(key)
            value = {"changed": 1}
        elif path == "sales-control/missing":
            show = parse_qs(parsed.query).get("hidden") == ["true"]
            items = [
                x
                for x in rows
                if ((x["department"], x["sales_date"]) in hidden) == show
            ]
            value = dict(
                items=items,
                total=len(items),
                page=1,
                date_from="2026-10-13",
                date_to="2026-11-11",
            )
        elif path.startswith("import-control/"):
            raise AssertionError("Retired API request " + path)
        r.fulfill(status=200, content_type="application/json", body=json.dumps(value))

    page.route("**/*", route)
    page.goto(url, wait_until="domcontentloaded")
    page.get_by_role("heading", name="Контроль продаж", exact=True).wait_for()
    page.get_by_text("11.11.2026", exact=True).wait_for()
    assert not writes
    page.screenshot(path=str(out / "control-desktop.png"), full_page=True)
    page.get_by_label("Выбрать Тестовый магазин Север 11.11.2026").check()
    page.get_by_role("button", name="Удалить", exact=True).click()
    page.get_by_role("dialog").wait_for()
    page.keyboard.press("Escape")
    assert page.get_by_role("dialog").count() == 0 and not writes
    page.get_by_role("button", name="Удалить", exact=True).click()
    page.get_by_role("dialog").get_by_role("button", name="Удалить", exact=True).click()
    page.get_by_text("Найдено: 1", exact=True).wait_for()
    assert len(writes) == 1
    page.get_by_label("Показать скрытые").check()
    page.get_by_label("Выделить все строки текущей страницы").check()
    page.get_by_role("button", name="Восстановить", exact=True).click()
    page.get_by_role("dialog").get_by_role(
        "button", name="Восстановить", exact=True
    ).click()
    page.get_by_text("По выбранным условиям записей не найдено", exact=True).wait_for()
    page.get_by_label("Показать скрытые").uncheck()
    page.get_by_role("button", name="Настройки уведомлений", exact=True).click()
    page.get_by_label("Получатели уведомлений", exact=True).fill(
        "staff@example.invalid"
    )
    page.get_by_role("button", name="Добавить", exact=True).click()
    page.get_by_label("Автоматический контроль продаж и email-уведомления").check()
    assert len(writes) == 2
    page.get_by_role("button", name="Сохранить настройки", exact=True).click()
    page.get_by_text("Настройки уведомлений сохранены", exact=True).wait_for()
    page.screenshot(path=str(out / "notifications-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(out / "notifications-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth+1")
    page.get_by_role("button", name="Контроль продаж", exact=True).click()
    page.get_by_text("11.11.2026", exact=True).wait_for()
    page.screenshot(path=str(out / "control-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth+1")
    assert not errors, errors
    assert writes == [
        ("POST", "sales-control/exclusions"),
        ("DELETE", "sales-control/exclusions"),
        ("PUT", "sales-control/notification-settings"),
    ], writes
    browser.close()
print(
    "PASS: desktop/mobile, no old APIs, reads only on open, logical hide/restore, explicit save, Escape, no overflow/JS errors"
)
