# Chromium Crashpad: ошибка запуска PDF

## Подтверждено production-выводом оператора

Образ sales-backend:pr173-08bafa7; UID10001(app); Chromium156.0.8078.79 Debian13.
Проба tiny PDF показала available RAM2101092352байт, stage=launch,
TargetClosedError, `chrome_crashpad_handler: --database is required`, SIGTRAP.
Обработка PDF падала примерно за3.1с, не за общий90с timeout.
Это конкретный сбой запуска Crashpad; общий отказ памяти512МиБ не был причиной
этой пробы. OOMKilled=false не исключает любые другие OOM в любой момент.

Точный набор HOME/XDG/CHROME_CONFIG_HOME production не получен. Нельзя утверждать,
что одна отсутствующая /home/app сама по себе воспроизводит сбой. В Dockerfile
app создаётся без home, но чистый локальный контейнер с тем же UID/Chromium без
home успешно запускался и до исправления. Параметры окружения текущего сервера
могут отличаться; требуется указанная ниже проба перед установкой.

Chromium хранит Crash Reports относительно default config/user-data directory,
а не обязательно переданного Playwright --user-data-dir. На Linux
CHROME_CONFIG_HOME имеет приоритет над XDG_CONFIG_HOME. Источники upstream:
- https://github.com/chromium/chromium/blob/main/chrome/common/chrome_paths.cc (DIR_CRASH_DUMPS)
- https://github.com/chromium/chromium/blob/main/chrome/common/chrome_paths_linux.cc (GetDefaultUserDataDirectory)

## Изменение в PR174

На каждый PDF создаётся отдельный TemporaryDirectory. Только дочернему Chromium
передаётся окружение с private HOME/XDG_CONFIG_HOME/XDG_CACHE_HOME/XDG_RUNTIME_DIR/
CHROME_CONFIG_HOME. Все каталоги0700, принадлежат текущему пользователю. После
закрытия браузера они удаляются, в том числе при исключениях/отмене. Окружение
Python backend не меняется. Profile/cookies между отчётами не разделяются.

Это устраняет зависимость процесса браузера от доступности default home/config.
Не меняются пользователь контейнера, Dockerfile/Compose, БД, импорты, память,
таймауты, browser security flags, offline/CSP/SSRF и проверки снимков/фотографий.
Ошибки создания временной среды возвращают контролируемую503 без raw paths.
Проба в production с этими переменными пока ожидается; успешный local PDF не
объявляется доказательством исправления конкретного production окружения.

## Одноразовая проба ДО пересборки

Оператор выполняет вручную. Создаёт один крошечный PDF в памяти, не вызывает
аналитические API/БД и не меняет окружение работающего backend. Каталоги удаляются
после закрытия browser. Если свободной RAM меньше512МиБ, запуск не производится.

```bash
sudo docker exec -i sales-journal-backend python -u - <<'PY'
import asyncio, os, tempfile
from pathlib import Path
from playwright.async_api import async_playwright
from app.services.product_analytics_runtime import available_memory

async def check():
    available = available_memory()
    if available is not None and available < 512*1024*1024:
        print('STOP: недостаточно свободной RAM', available)
        return
    with tempfile.TemporaryDirectory(prefix='pdf-home-check-') as root:
        env = dict(os.environ)
        for key in ('HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_RUNTIME_DIR','CHROME_CONFIG_HOME'):
            path = Path(root)/key
            path.mkdir(mode=0o700)
            env[key] = str(path)
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=os.environ.get('REPORT_PDF_CHROMIUM','/usr/bin/chromium'),
                headless=True, env=env,
                args=['--disable-dev-shm-usage','--disable-gpu','--no-first-run'])
            try:
                context = await browser.new_context(offline=True, java_script_enabled=False)
                page = await context.new_page()
                await page.set_content('<h1>PDF check</h1>')
                pdf = await page.pdf(format='A4')
                print('OK: PDF bytes =', len(pdf))
            finally:
                await browser.close()
asyncio.run(check())
PY
```

Если получится OK, исправление окружения подтверждено на текущем контейнере.
Если ошибка сохраняется, PR оставляется draft и причина уточняется по новому выводу;
не добавлять случайные Chromium flags, не понижать memory thresholds и не менять
права /root, Docker socket или security policy сервера.

## Локальные проверки

Chromium156.0.8078.79 извлечён из официальных Debian-пакетов в отдельный workspace;
системный Chromium151 не заменялся. Дополнительно создан только локальный тестовый
контейнер Python3.12/Debian13/Chromium156: UID10001 безhome, read-only root,
network=none,1CPU,768МиБ. Новый запуск создал PDF6035байт. Baseline в чистом
контейнере тоже прошёл: точный production-сбой не воспроизведён.

Профильный прогон в отдельном локальном контейнере с Chromium156: **18 passed**.
Python-тесты запущены непривилегированным UID1000 (владелец read-only test mounts);
отдельная tiny-PDF проба выполнена UID10001, как указано выше.
Тесты проверяют0700, неизменность родительского окружения, cleanup при успехе и
исключении, контролируемую503 при недоступном temp и настоящий PDF с плохим
родительским HOME/XDG/CHROME_CONFIG_HOME. Фактические результаты указаны в PR.

Полный host-прогон1037passed/113skipped имел один отказ нового native теста из-за
реально доступного RAM<512МиБ после локальной сборки тестового образа. Лимит не
обходился и не понижался. Native проверка выполняется отдельно в изолированном
тестовом контейнере с достаточной памятью. Это ограничение local runner, не
свидетельство регрессии вычислений и не проверка production.

## Установка

Для исправления запуска достаточно нового **backend image**, собранного вне VPS
из согласованного коммита после review/merge. Frontend-изменения предыдущего коммита
PR174 нужны для подробной диагностики, но не являются условием устранения
Crashpad. Миграций/настроек RAM/Swap не требуется. Production не обновлялся;
автоматического merge/deploy нет. PR остаётся draft до подтверждения пробы.
