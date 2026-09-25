# Журнал продаж

Production-ready веб-сервис для накопления продаж из XLS/XLSX/HTML, ведения реестра и аналитики. Все показатели считаются по PostgreSQL, mock-данных нет. Приложение рассчитано на публикацию в подпапке `https://kvasmix.ru/vr/sales/`.

## Возможности первой версии

- фоновая загрузка одного или нескольких `.xls`/`.xlsx`/`.html`/`.htm`, сохранение оригиналов;
- поиск листа и строки заголовков (первые 1000 строк), поддержка многострочной шапки и сопоставление колонок независимо от порядка и пунктуации;
- нормализация дат, `Decimal`, процентов, телефонов и пустых значений;
- разбор товарных строк и сохранение исходного поля `Товары`;
- отчет «Динамика продаж» с KPI, сравнением с предыдущим периодом, серверной группировкой по дням/неделям/месяцам и фильтрами ClientsVR;
- SHA-256 fingerprint из даты, документа, подразделения и суммы + уникальное ограничение БД;
- построчная изоляция ошибок, журнал и статистика импортов;
- серверные поиск, фильтрация, сортировка и пагинация (20/50/100/200);
- карточка продажи со всеми полями и отдельной таблицей товаров;
- обзор KPI и сопоставимый прошлый период, динамика, магазины, клиенты, скидки, акции и чеки;
- кликабельный статус неудачного импорта с общим сообщением, ошибками строк и журналом обработки;
- вкладка «Настройки» с быстрым копированием пути `/var/www/html/vr/update_sales.sh`;
- адаптивный React UI без тестовых данных.

Модели `Sale` ↔ `SaleItem` сохраняют состав чека и позволяют добавить ABC/XYZ, RFM, товарные связки и аномалии без изменения базовой архитектуры.

## Стек и структура

- `backend/app/models`, `schemas`, `routers`, `services`, `repositories`, `importer`, `analytics` — FastAPI, SQLAlchemy 2 async;
- `backend/alembic` — миграции PostgreSQL 16;
- `frontend/src/pages`, `components`, `api`, `hooks`, `types`, `utils` — React, TypeScript, Vite, Recharts;
- `docker-compose.yml` — PostgreSQL, API и статический Nginx;
- `deploy/nginx-location.conf` — фрагмент конфигурации внешнего Nginx.

## Настройка и локальный запуск

```bash
cp .env.example .env
# обязательно задайте длинный POSTGRES_PASSWORD
docker compose up -d --build
docker compose ps
```

Frontend: `http://127.0.0.1:8011/vr/sales/`, API: `http://127.0.0.1:8010/api/docs`. Порты слушают только loopback. Миграция `alembic upgrade head` выполняется backend-контейнером перед стартом. Вручную:

```bash
docker compose exec backend alembic current
docker compose exec backend alembic upgrade head
```

Для разработки frontend:

```bash
cd frontend
npm install
VITE_BASE_PATH=/vr/sales/ npm run dev
```

## Импорт

Откройте **Импорт**, выберите файлы и нажмите «Начать импорт». API отвечает `202`, обработка продолжается фоновой задачей, UI обновляет историю каждые 5 секунд. Оригиналы находятся в Docker volume `sales_imports`. Для товара надёжно поддержан формат:

```text
артикул | код | наименование | количество | базовая цена | цена продажи
```

Также распознаётся `Название x 2 по 100`; неизвестная строка не теряется и сохраняется как название и `extra_data`. Из-за отсутствия формальной спецификации поля это безопасная расширяемая стратегия. Ошибки доступны во всплывающей подсказке счётчика истории и через `GET /api/imports/{id}`.

Поддерживается формат штатной выгрузки `Артикул: ... | Код: ... | Наименование: ... | Количество: ... | Цена базовая: ... | Цена: ...`. Несколько позиций разделяются переносом строки либо последовательностью `| Артикул:`. Для старых XLS с повреждённой кодировкой шапки предусмотрен безопасный резервный разбор показанной 18-колоночной структуры; он включается только когда следующая строка похожа на продажу по дате, документу, подразделению и сумме.

Проценты из числовых Excel-ячеек (`-0.17`) переводятся в процентное представление (`-17`), а текстовые значения (`-17%` или `-15`) сохраняют свой масштаб. Миграция `20260915_03` автоматически исправляет ранее загруженные отрицательные скидки, ошибочно увеличенные в 100 раз.

Если в строке продажи указана оплата сертификатом, её сумма вычитается из выручки. В журнале обе части платежа показываются в формате `сумма + сертификат`. Миграция `20260921_05` применяет то же правило к ранее загруженным продажам.

> BackgroundTasks подходит для первой версии и не блокирует HTTP/UI. Для нескольких backend-реплик или очень тяжёлых файлов сервис импорта следует без изменения REST-контракта вынести в Celery/RQ.

### Автозагрузка с FTP

В меню **Настройки → FTP** сохраняются параметры FTP/FTPS, проверяется подключение и доступна история запусков. Backend ежедневно в `23:55` по часовому поясу `AUTOLOAD_TIMEZONE` проверяет указанный каталог и обрабатывает все `.xls`/`.xlsx`/`.html`/`.htm`, включая ранее помеченные `EROOR_` файлы для повторной попытки после исправления причины ошибки. После полностью успешного импорта удалённый файл удаляется. При первой ошибке файл остаётся на FTP и переименовывается с префиксом `EROOR_`; при повторной ошибке дополнительный префикс не добавляется. Документы с номерами, начинающимися на `ВЗВ-`, `РНВ-` или `ВРМ-`, учитываются как пропущенные и в журнал продаж не добавляются.

Для ручного и автоматического импорта действует единое специальное правило: если название файла содержит слово «Авиаторов», импортируются только строки, где колонка «Подразделение» равна «Авиаторов». Для остальных файлов импортируются все строки.

### Интеграция с реестром клиентов

Фильтр «Менеджер» в журнале получает менеджеров и закреплённых за ними клиентов из сервиса `clients_vr`. Базовый адрес задаётся переменной `CLIENTS_VR_API_URL` (по умолчанию `https://kvasmix.ru/vr/clients/api`), а при защищённом API токен передаётся через `CLIENTS_VR_API_TOKEN`. Backend проксирует запросы, кэширует ответы на 5 минут и фильтрует продажи по полученному списку клиентов; секретный токен не передаётся в браузер.

## REST API

Swagger доступен по `/vr/sales/api/docs` через внешний proxy. Основные ресурсы: `/api/sales`, `/api/sales/{id}`, `/api/sales/{id}/items`, `/api/imports`, `/api/analytics/{overview,dynamics,departments,products,clients,discounts,promotions,checks}`, `/api/filters`, `/api/health`.

Поиск выполняется в продажах и через `EXISTS` в товарах; индексы созданы для дат, документов, подразделений, клиентов, телефонов, карт, акций, fingerprint, внешних ключей и товарных атрибутов. Для десятков миллионов строк рекомендуется добавить PostgreSQL `pg_trgm` GIN-индексы отдельной миграцией после анализа реальных запросов.

## Развёртывание на Timeweb

```bash
sudo mkdir -p /var/www/html/vr/sales
sudo chown "$USER":"$USER" /var/www/html/vr/sales
cd /var/www/html/vr/sales
git clone <URL_РЕПОЗИТОРИЯ> .
cp .env.example .env
nano .env
docker compose up -d --build
docker compose exec backend alembic current
```

Сохраните точные значения:

```dotenv
BASE_PATH=/vr/sales
VITE_BASE_PATH=/vr/sales/
CORS_ORIGINS=https://kvasmix.ru
```

Содержимое `deploy/nginx-location.conf` поместите **в существующий** HTTPS `server {}` домена `kvasmix.ru`, затем:

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -fsS https://kvasmix.ru/vr/sales/api/health
```

`proxy_pass` API удаляет внешний префикс, а FastAPI формирует документацию с `root_path`. Frontend собран с Vite `base=/vr/sales/`; BrowserRouter использует тот же basename. Внутренний Nginx делает SPA fallback на `/vr/sales/index.html`, поэтому прямое открытие и обновление frontend routes не дают 404, а assets загружаются из `/vr/sales/assets/`, не из `/assets/`.

## Обновление, резервное копирование и восстановление

```bash
cd /var/www/html/vr/sales
git pull --ff-only
docker compose build --pull
docker compose up -d
docker compose exec backend alembic upgrade head

# backup БД
docker compose exec -T db pg_dump -U sales -Fc sales > sales-$(date +%F).dump
# restore в пустую БД
docker compose exec -T db pg_restore -U sales -d sales --clean --if-exists < sales.dump
# backup оригиналов
docker run --rm -v sales-journal_sales_imports:/data -v "$PWD":/backup alpine tar czf /backup/imports.tgz -C /data .
```

Перед обновлением создавайте оба backup. Имена volumes уточняются через `docker volume ls`.

## Диагностика

```bash
docker compose ps
docker compose logs -f --tail=200 backend
docker compose logs -f --tail=100 db frontend
docker compose exec db pg_isready -U sales -d sales
docker compose exec backend python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/api/health').read())"
curl -I http://127.0.0.1:8011/vr/sales/analytics
```

Если импорт завершился `failed`, проверьте `error_text`, права volume и логи backend. Чувствительные поля строк не выводятся в штатный лог. PostgreSQL и uploads — persistent volumes и не удаляются при обычном `docker compose down`; команда `down -v` **удалит данные**.

### Интеграция с vrcatalog

Для сценария «Продажи HoReCa» задайте `VRCATALOG_API_URL` и при необходимости `VRCATALOG_API_TOKEN`. Sales Journal запрашивает `GET /products` (с резервным `GET /catalog/products`) с параметрами `property=HoReCa`, `property_value=HoReCa`, `limit=10000`. Ответ может содержать массив в `products`, `items`, `data` или `results`; для сопоставления используются `article`/`sku` и `code`. Найденные товары исключаются из XLSX-отчета.

## Отчет «Продажи по товарам»

Страница доступна по `/vr/sales/reports/product-sales`. Поиск товаров выполняется по данным `SaleItem`, а параметры отчета передаются в backend через POST JSON. Агрегация, сравнение периодов, динамика, разрезы менеджеров/подразделений и Excel выполняются на сервере. Сохраненные наборы находятся в PostgreSQL.

Автоматический подбор по произвольным фильтрам CatalogVR намеренно не подменяется фиктивными справочниками: текущему контракту CatalogVR не хватает endpoint метаданных доступных фильтров и серверного поиска по их значениям. `/api/reports/product-sales/catalog/filters` возвращает понятный HTTP 501 до появления такого API. Минимально необходимое расширение CatalogVR: endpoint метаданных фильтров и POST-поиск, возвращающий стабильные `code`/`article` с серверной пагинацией или идентификатором набора.

### Ссылочный отчет HoReCa

Сценарий «Продажи HoReCa» формирует защищенную случайным токеном ссылку вида `/vr/sales/reports/horeca/{token}` и отправляет ее основным получателям. Страница показывает количество продаж за отчетный период и три календарных месяца, позволяет выбрать товары и отправить XLSX со списком в ОМиР на отдельные адреса обратного письма. Публичный адрес приложения задается через `PUBLIC_URL`. До расширения CatalogVR данными изображений колонка «Фото» показывает безопасный placeholder; фиктивные URL изображений не создаются.

## Проверки разработчика

```bash
cd backend && pytest
cd frontend && npm run build
docker compose config
docker compose build
```
