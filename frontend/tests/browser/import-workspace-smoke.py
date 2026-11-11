"""Local UI only: all API calls synthetic, no backend/production/import/email."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

url = os.environ.get("IMPORT_UI_URL", "http://127.0.0.1:18528/vr/sales/imports")
assert urlsplit(url).hostname in ("127.0.0.1", "localhost")
artifacts = Path(os.environ.get("IMPORT_UI_ARTIFACTS", "/tmp/import-workspace-ui"))
artifacts.mkdir(parents=True, exist_ok=True)
mutations = []
errors = []


def batch(i, name, status):
    return dict(
        id=i,
        filename=name,
        status=status,
        file_size=2048,
        uploaded_at="2026-11-11T10:00:00Z",
        period_start="2026-11-10",
        period_end="2026-11-10",
        total_rows=150,
        processed_rows=150,
        added_rows=140,
        duplicate_rows=10,
        skipped_rows=0,
        error_rows=1 if status == "failed" else 0,
        duration_ms=1200,
        errors=[],
    )


items = [
    batch(1, "fixture_10.11.2026.html", "completed"),
    batch(2, "fixture-error.html", "failed"),
]
config = dict(
    enabled=True,
    automatic=False,
    email_enabled=False,
    start_date="2026-11-11",
    recipients="",
    revision=1,
)
departments = [
    dict(
        id=i,
        code=f"SYNTH-{i:02}",
        name=f"Тестовое подразделение {i:02}",
        rules=[
            dict(
                id=i,
                start_date="2019-09-21",
                end_date=None,
                filename_template=f"fixture_{i}_{{date}}.html",
                date_format="dmy",
                active=True,
                controlled=True,
            )
        ],
    )
    for i in range(1, 13)
]
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium",
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="ru-RU")
    page.on("pageerror", lambda e: errors.append(str(e)))

    def route(request):
        req = request.request
        parsed = urlsplit(req.url)
        if parsed.hostname not in ("127.0.0.1", "localhost"):
            return request.abort()  # Existing font URLs also stay offline.
        if not parsed.path.startswith("/vr/sales/api/"):
            return request.continue_()
        path = parsed.path.split("/api/", 1)[1]
        if req.method != "GET":
            mutations.append((req.method, path))
        value = []
        if path == "settings/auth/session":
            value = {}
        elif path == "imports" and req.method == "POST":
            items.append(batch(3, "upload-fixture.html", "queued"))
        elif path == "imports":
            value = items
        elif path == "imports/2":
            value = {
                **items[1],
                "error_text": "Синтетическая ошибка формата",
                "log_text": "Без производственных данных",
                "errors": [dict(id=1, row_number=2, message="Нет обязательного поля")],
            }
        elif path == "import-control/status":
            value = dict(
                enabled=True,
                latest=dict(
                    status="incomplete",
                    sales_date="2026-11-10",
                    expected=12,
                    successful=10,
                    missing=2,
                    errors=0,
                    checked_at="2026-11-11T00:21:00Z",
                ),
                open_counts=dict(missing=2, error=0),
            )
        elif path == "import-control/settings":
            value = config
        elif path == "import-control/departments":
            value = departments
        elif path == "import-control/historical/status":
            value = dict(status="not_started", checked=0, total=0, plan=[])
        elif path == "import-control/template-repair/preview":
            value = dict(
                token="a" * 64,
                items=[
                    dict(
                        rule_id=1,
                        department_code="SYNTH-01",
                        start_date="2019-09-21",
                        end_date=None,
                        before="fixture_09.11.2026.html",
                        after="fixture_{date}.html",
                    )
                ],
            )
        request.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(value, ensure_ascii=False),
        )

    page.route("**/*", route)

    def capture(name):
        page.evaluate("window.scrollTo(0,0)")
        page.screenshot(path=str(artifacts / name), full_page=True)

    page.goto(url, wait_until="domcontentloaded")
    page.get_by_text("Неполный импорт", exact=True).wait_for()
    assert mutations == []
    capture("control-desktop.png")
    page.get_by_role("button", name="Загрузка файлов", exact=True).click()
    page.locator("#imports-files").set_input_files(
        [
            dict(name="upload-fixture.html", mimeType="text/html", buffer=b"synthetic"),
            dict(
                name="remove.xls",
                mimeType="application/vnd.ms-excel",
                buffer=b"synthetic",
            ),
        ]
    )
    page.get_by_role("button", name="Убрать файл remove.xls", exact=True).click()
    assert mutations == []
    capture("upload-desktop.png")
    page.get_by_role("button", name="Начать импорт", exact=True).click()
    page.get_by_text("Файлы переданы на обработку", exact=True).wait_for()
    assert mutations == [("POST", "imports")]
    page.get_by_role("button", name="Перейти к журналу", exact=True).click()
    page.get_by_text("upload-fixture.html", exact=True).wait_for()
    page.get_by_label("Поиск по имени файла").fill("fixture-error")
    page.get_by_label("Статус обработки").select_option("failed")
    trigger = page.get_by_role("button", name="Ошибки: 1", exact=True)
    trigger.click()
    dialog = page.get_by_role("dialog", name="Детали ошибки", exact=True)
    dialog.get_by_text("Нет обязательного поля", exact=True).wait_for()
    page.keyboard.press("Escape")
    assert not dialog.is_visible()
    page.wait_for_function("document.activeElement?.textContent.includes('Ошибки: 1')")
    capture("journal-desktop.png")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Контроль полноты", exact=True).click()
    capture("control-mobile.png")
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
    page.get_by_role("button", name="Историческая сверка", exact=True).click()
    page.get_by_text("Исправление исторических шаблонов", exact=True).click()
    page.get_by_role(
        "button", name="Подготовить отчёт исправления шаблонов", exact=True
    ).click()
    page.get_by_text("fixture_{date}.html", exact=True).wait_for()
    assert page.get_by_role(
        "button", name="Применить выбранные исправления", exact=True
    ).is_disabled()
    capture("history-mobile.png")
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
    page.get_by_role("button", name="Загрузка файлов", exact=True).click()
    capture("upload-mobile.png")
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
    assert mutations == [("POST", "imports")], mutations
    assert not errors, errors
    browser.close()
    print(
        "PASS: desktop/mobile workspaces, explicit upload only, native dialog Escape/focus return, read-only repair, no overflow/JS errors"
    )
