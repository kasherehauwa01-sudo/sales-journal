"""Optional local Chromium smoke: every API response is synthetic, no backend needed."""

import json, os
from pathlib import Path
from urllib.parse import urlsplit

url = os.environ.get("IMPORT_CONTROL_UI_URL", "http://127.0.0.1:18528/vr/sales/imports")
assert urlsplit(url).hostname in {
    "127.0.0.1",
    "localhost",
}, "Synthetic UI smoke must never target production"
artifacts = Path(
    os.environ.get("IMPORT_CONTROL_UI_ARTIFACTS", "/tmp/import-control-ui")
)
artifacts.mkdir(parents=True, exist_ok=True)
from playwright.sync_api import sync_playwright

config = dict(
    enabled=False,
    automatic=False,
    email_enabled=False,
    start_date=None,
    day_offset=None,
    marker_template=None,
    recipients="",
    revision=1,
)
issue = dict(
    id=1,
    sales_date="2026-11-08",
    department_name="Тестовое подразделение",
    department_id=1,
    expected_filename="fixture_08.11.2026.html",
    status="missing",
    checked_at="2026-11-09T00:00:00Z",
)
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=os.environ.get("CHROMIUM_EXECUTABLE"),
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    mutations = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def api(route):
        path = route.request.url.split("/api/", 1)[-1]
        if route.request.method != "GET":
            mutations.append(path)
        if path == "import-control/status":
            value = dict(
                enabled=False, latest=None, open_counts=dict(missing=2, error=0)
            )
        elif path == "import-control/settings":
            value = config
        elif path == "import-control/departments":
            value = []
        elif path.startswith("import-control/issues?"):
            value = dict(items=[issue], total=1, page=1)
        elif path == "settings/auth/session":
            value = {}
        else:
            value = []
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(value, ensure_ascii=False),
        )

    page.route("**/vr/sales/api/**", api)
    page.goto(url)
    page.get_by_text("Контроль отключён", exact=True).wait_for()
    trigger = page.get_by_role("button", name="Не загружено 2 файла")
    trigger.click()
    dialog = page.get_by_role("dialog", name="Отсутствующие файлы импорта")
    dialog.get_by_text("Тестовое подразделение", exact=True).wait_for()
    for _ in range(24):
        page.keyboard.press("Tab")
        assert page.evaluate(
            'document.querySelector("dialog").contains(document.activeElement)'
        )
    page.screenshot(path=str(artifacts / "desktop.png"), full_page=True)
    page.keyboard.press("Escape")
    assert not dialog.is_visible()
    page.wait_for_function(
        "document.activeElement?.textContent.includes('Не загружено 2 файла')"
    )
    assert trigger.evaluate("(node)=>node===document.activeElement")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Подразделения", exact=True).click()
    page.get_by_role("button", name="Добавить подразделение", exact=True).click()
    dialog = page.get_by_role("dialog", name="Добавить подразделение")
    assert dialog.is_visible()
    assert dialog.evaluate("(node)=>node.getBoundingClientRect().width") <= 390
    page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
    assert mutations == [], mutations
    assert errors == [], errors
    print(
        "Chromium: desktop/mobile, native dialog, 24 focus-cycle steps, Escape/focus restore, no automatic writes: PASS"
    )
    browser.close()
