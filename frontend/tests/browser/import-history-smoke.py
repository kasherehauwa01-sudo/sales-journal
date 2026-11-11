"""Local Chromium + synthetic API, never backend/production/import/SMTP."""

import json, os
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

url = os.environ.get("IMPORT_HISTORY_UI_URL", "http://127.0.0.1:18528/vr/sales/imports")
assert urlsplit(url).hostname in {"127.0.0.1", "localhost"}
artifacts = Path(
    os.environ.get("IMPORT_HISTORY_UI_ARTIFACTS", "/tmp/import-history-ui")
)
artifacts.mkdir(parents=True, exist_ok=True)
job = {"status": "not_started", "checked": 0, "total": 0, "plan": []}
mutations = []
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium",
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def api(route):
        global job
        path = route.request.url.split("/api/", 1)[-1]
        if route.request.method != "GET":
            mutations.append(path)
        value = []
        if path == "import-control/status":
            value = {
                "enabled": False,
                "latest": None,
                "open_counts": {"missing": 0, "error": 0},
            }
        elif path == "import-control/settings":
            value = {
                "enabled": False,
                "automatic": False,
                "email_enabled": False,
                "start_date": "2026-11-11",
                "recipients": "",
                "revision": 1,
            }
        elif path == "import-control/departments":
            value = [
                {
                    "id": 1,
                    "name": "Синтетический магазин",
                    "code": "SYNTH",
                    "rules": [{"start_date": "2019-09-21"}],
                }
            ]
        elif path == "settings/auth/session":
            value = {}
        elif path == "import-control/historical/status":
            value = job
        elif path == "import-control/historical/start":
            job = {
                "status": "running",
                "checked": 0,
                "total": 2609,
                "plan": [
                    {
                        "department_id": 1,
                        "first_rule_date": "2019-09-21",
                        "date_from": "2019-09-21",
                        "date_to": "2026-11-11",
                    }
                ],
            }
            value = job
        elif path == "import-control/historical/pause":
            job = {**job, "status": "paused"}
            value = job
        elif path == "import-control/historical/resume":
            job = {**job, "status": "running", "checked": 1}
            value = job
        elif path.startswith("import-control/historical/results?"):
            value = {
                "page": 1,
                "total": 1,
                "items": [
                    {
                        "sales_date": "2026-11-09",
                        "department_id": 1,
                        "department_name": "Синтетический магазин",
                        "department_code": "SYNTH",
                        "expected_filename": "fixture_09.11.2026.html",
                        "status": "missing",
                        "checked_at": "2026-11-11T08:00:00Z",
                    }
                ],
            }
        elif path == "import-control/template-repair/preview":
            value = {
                "token": "a" * 64,
                "items": [
                    {
                        "rule_id": 1,
                        "department_code": "SYNTH",
                        "start_date": "2019-09-21",
                        "end_date": None,
                        "before": "fixture_09.11.2026.html",
                        "after": "fixture_{date}.html",
                    }
                ],
            }
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(value, ensure_ascii=False),
        )

    page.route("**/vr/sales/api/**", api)
    page.goto(url)
    page.get_by_role("button", name="Историческая сверка", exact=True).click()
    section = page.get_by_role("region", name="Историческая сверка импорта")
    section.get_by_text("21.09.2019", exact=True).wait_for()
    assert mutations == []
    assert section.get_by_role("button", name="Начать сверку с правил").is_disabled()
    section.get_by_label(
        "Подтверждаю запуск или новый пересчёт выбранной истории"
    ).check()
    section.get_by_role("button", name="Начать сверку с правил").click()
    section.get_by_text("21.09.2019 — 11.11.2026", exact=True).wait_for()
    section.get_by_role("button", name="Приостановить сверку").click()
    section.get_by_text("Состояние: Приостановлена.", exact=False).wait_for()
    section.get_by_role("button", name="Продолжить сверку").click()
    section.get_by_role("button", name="Показать результаты сверки").click()
    section.get_by_text("fixture_09.11.2026.html", exact=True).wait_for()
    page.evaluate("window.scrollTo(0,0)")
    page.screenshot(path=str(artifacts / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    section.get_by_text("Исправление исторических шаблонов", exact=True).click()
    section.get_by_role("button", name="Подготовить отчёт исправления шаблонов").click()
    section.get_by_text("fixture_{date}.html", exact=True).wait_for()
    assert section.get_by_role(
        "button", name="Применить выбранные исправления"
    ).is_disabled()
    assert page.evaluate(
        "document.documentElement.scrollWidth<=window.innerWidth"
    ), "Body overflow on mobile"
    page.evaluate("window.scrollTo(0,0)")
    page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
    assert mutations == [
        "import-control/historical/start",
        "import-control/historical/pause",
        "import-control/historical/resume",
    ], mutations
    assert not errors, errors
    browser.close()
    print(
        "Chromium: desktop/mobile, no auto-start, explicit start/pause/resume, results, read-only repair preview: PASS"
    )
