# Calltrack: исходная история покупок v1

## Источники и совместимость

Sales Journal main deb147e: `routers/calltrack_integration.py:require_calltrack_token`,
`services/client_identity.py:analytical_name_sql`, `services/analytics_scope.py`,
`services/analytics_signal_worker.py:scan_batch` изучены. Отдельные маршруты
identity-history отсутствовали; существующий analytics router уже подключён в main.
Режим SHADOW, политика aviatorov-wholesale-corporate/2 и ограничения automation не меняются.

Calltrack main d43d77f453bb747c4670cea0ecd02a07fd5474f2:
- `api/analytics_verified_review.php`17–27 получает summary перед каждой страницей,
  хранит hash/after/seen в session, требует полное чтение перед confirm (43).
- `api/analytics_verified_bindings.php:analyticsReviewHistory`82–84 проверяет
  version/department/normalization/key/count>=1/SHA256. Нулевой count не подтверждается.
- `analizmop/manager-tasks.js`277–286 отображает first_purchase/last_purchase и
  **JSON.stringify каждой строки**: обязательного старого набора колонок там нет.
- `tests/verified_pipeline_e2e.php` найден в backup/production-server-20261028,
  commit4b5cb490ea9bde50b2fbd056ff4400a581da1a3d, blob8b7c69dc891dce9c919ff14ee109ca1c15b73fb3.
  Его prepare (25–29) сравнивает строки строго с независимо заданным expected_history.
  В текущем main этого файла нет. Изучен код, но PHP/MariaDB E2E **не запускался**.

Это сверка с доступным кодом, не утверждение установленной версии VPS и не
согласование владельцем Calltrack. Репозиторий/production Calltrack не менялись.

## Маршруты

Оба GET под прежним Bearer `require_calltrack_token`:

- `/api/integrations/calltrack/analytics/identity-history?client_key=...`
- `/api/integrations/calltrack/analytics/identity-history/rows?client_key=...&history_hash=...&after=0&page_size=50`

client_key — **уже нормализованный v1** ключ из subject.kind=normalized_name,
subject.version=1. Непустой, максимум512символов, idempotent по v1. Неканоническое
имя возвращает422, не становится нечётким запросом. SQL сравнивает v1 expression
`lower(trim(regexp_replace(client, <existing explicit whitespace alphabet>, ' ', 'g')))`
с ключом **точным равенством**, не LIKE/phone/legacy casefold. NBSP/повторы пробелов
и регистр объединяются строго по существующей версии1. Например Straße и STRASSE
имеют **разные** ключи v1. NULL/пустой client в совпадение не попадает.

Всегда `sales.department='Авиаторов'`, все даты, все документы: включая возвраты,
нулевые/отрицательные суммы, поздние импорты старых дат. Не таблицы AnalyticsPurchaseDay,
AnalyticsSignalRun, агрегаты или текущий каталог покупателей. Текущая buyer type
может меняться, поэтому history не отсекает старые продажи по текущей категории
ClientsVR. Scope policy в ответе — контекст evidence; endpoint не удостоверяет
принадлежность покупателя к опту/корпоративным и не разрешает automation/привязку.
Соответствие approved scope/клиенту проверяет прежний Calltrack review.

### Summary

```json
{
  "version": 1,
  "department": "Авиаторов",
  "normalization_version": 1,
  "client_key": "ооо радуга",
  "sale_count": 3,
  "history_hash": "64 lowercase hex characters",
  "first_purchase": "2020-01-01",
  "last_purchase": "2026-11-01",
  "last_positive_purchase": "2026-10-01",
  "empty": false,
  "scope_policy": "aviatorov-wholesale-corporate/2",
  "mode": "SHADOW",
  "automation_allowed": false
}
```

last_positive_purchase — max sale_date при total_amount>0 и document_number **не**
начинается с `ВЗВ-`, `РНВ-`, `ВРМ-` (регистр важен): как существующий pilot worker.
first_purchase/last_purchase — min/max дат всех исходных документов, не ID-порядок.
Пустая/неизвестная группа:200, count0, empty=true, датыnull, валидный hash пустого
набора данного ключа. Calltrack analyticsReviewHistory отклоняет count0; пустота
никогда не подменяет ошибку503 и не может подтвердить клиента.

### Rows и курсор

Ответ содержит **все поля summary**, `items: list`, `next_after: int|null`.
Порядок `Sale.id ASC`, after — исключительная нижняя граница ID, 0 — начало.
page_size1..100, default50. next_after — ID последней выданной строки, только если
есть следующая; последний ответnull. ID положительные bigint, after0..2^63-1;
непринадлежащий группе after409. Аномальный исходный ID<=0 даёт503, а не тихую потерю строк.
Сортировка по дате не используется: backdated импорт получает новый ID и при этом
обязательно меняет полный hash. Размер страницы и after в hash не входят.

Строка — ровно перечисленные ниже поля (все всегда присутствуют, null сохраняется):

`sale_id`, `row_number`, `sale_date`, `document_number`, `client`, `department`,
`total_amount`, `base_amount`, `discount_percent`, `reason`, `author`, `price_type`,
`discount_card_percent`, `discount_card_number`, `social`, `certificate_amount`,
`promotion`, `phone`, `original_products_text`, `fingerprint`, `import_id`,
`created_at`, `updated_at`.

sale_id/row_number/import_id — JSON integers, social boolean. Decimal — **строки**
без float-преобразования/экспоненты/несущественных конечных нулей (`10.20`→`"10.2"`,
`-0.00`→`"0"`). ДатыISO YYYY-MM-DD; timestamps UTC с6знаками микросекунд иZ.
Остальные строковые поля исходные, включая варианты client в одной нормализованной
группе. Никакой новой нормализации их отображаемых значений. Авторизованный review
получает исходные headers; Sale.items не загружаются. Новый column Sale не должен
молча изменитьv1: тест состава полей потребует отдельного решения о версии.

Для E2E нужно задать expected_history именно этим контрактом из независимого
синтетического input, включая фиксированные fixture timestamps/decimal strings;
нельзя брать expected из самого API и называть это независимой проверкой.

## SHA256: sale-headers-jsonl/1

Поток UTF8 состоит из:

1. Одна строка JSON заголовка:
   `{version:1,normalization_version:1,department:"Авиаторов",client_key:<key>,canonicalization:"sale-headers-jsonl/1"}`.
2. Одна строка JSON на **каждый** Sale указанной группы, в Sale.id ASC, с точным
   составом/типами строки выше (исходный id переименован в sale_id).

Каждый объект: keys sorted по Python Unicode codepoints, separators `,`/`:`,
ensure_ascii=false, allow_nan=false, затем ровно LF. Переводы строк внутри строк
экранируются JSON. SHA256 обновляется заголовком и всеми строками, не отдельной
страницей. Unicode NFC/casefold для payload не добавляются. Числа не округляются
через Decimal context. NaN/Infinity не допускаются; nullable timestamps=null,
naive timestamp не выдаётся как достоверный UTC.

Любое изменение сохранённого header (включая author/phone/fingerprint/import_id/
updated_at/original_products_text), вставка/удаление/перенос между группами меняет
evidence. sale_items — отдельная дочерняя таблица, в этот header-контракт не входит.
Переимпорт с новыми IDs/metadata также меняет hash. Это content evidence, не номер
транзакции, подпись, авторизация или доказательство настоящей личности.

## Согласованность и обязательный патч Calltrack

Каждый HTTP — одна **REPEATABLE READ READ ONLY** транзакция, REPORT_SETTINGS60сек,
stream100headers за порцию, удерживается только страница+одна lookahead строка.
Hash, count, даты и rows получены из **одного прохода одного снимка**. Cursor закрывается
при ошибке/отмене, транзакция rollback; записи/таблицы/миграции/материализованный cache
не создаются. Existing heavy_operation/headroom оставлены: лишние конкуренты получают
контролируемую503, лёгкий event loop не блокируется синхронным обращением к сервисам.

Между HTTP один MVCC snapshot не удерживается. Поэтому `/rows` **обязательно**
принимает history_hash последней подтверждённой summary, включая первую страницу.
Каждый раз пересчитывает полный hash в новой RR-транзакции. Изменение истории409,
не частичная страница с видимостью успеха. Без hash422. Повторяйте с новой summary
и after0 вручную. Count сам по себе не обнаруживает UPDATE при прежнем числе строк.

Текущий PHP получает summary, а затем rows **без hash**: этого недостаточно при
изменении между двумя запросами. Ослабление защиты ради старого вызова не сделано.
Подготовлены, **не применены**:

- [interactive review patch](patches/calltrack-history-hash-review.patch), проверен
  `git apply --check` на d43d77f... Он передаёт snapshot hash, сверяет ответный hash/
  count/identity через analyticsReviewHistory, проверяет положительные монотонные IDs,
  корректный next_after и отсутствие превышения общего count до изменения session.
- [backup E2E patch](patches/calltrack-history-hash-e2e.patch), проверен на указанном
  blob8b7c69d...: передаёт hash и сверяет summary/page hash/count. Этот файл существует
  только в backup-ветке, не применять весь combined patch к main с отсутствующим файлом.

confirm в текущем PHP заново запрашивает summary и сверяет session history_hash —
это сохранить. Не менять бюджеты, подписи, approved policies, методы подтверждения
или SHADOW ради включения этих API. PHP/MariaDB E2E и развёрнутый потребитель ещё
не проверены: PR draft до согласования/теста изменения Calltrack. Проверены чтением
именно исходники трёх потребителей, не состояние серверных файлов.

## Проверки и нагрузка

Новые PostgreSQL/asyncpg HTTP-тесты используют только localhost DB
calltrack_history_test и schema history_<uuid>, создают только синтетические Sale.
Проверяют auth, aliases v1/NBSP/Unicode, разные нормализованные имена, соседние
подразделения, все виды документов, канонизацию, страницы, INSERT задним числом/
UPDATE/DELETE/перенос, изменение при параллельном импорте, отмену,57014→503 и retry.
Есть тест группы250документов через несколько streaming batches и HTTP-страниц.
**129 passed,12,35сек**: новая история + существующие Calltrack integration,
analytics_scope, analytics_delivery, churn_pilot. Изолированные PostgreSQL/asyncpg,
FastAPI ASGI, синтетические схемы. Black для нового service/test, AST и
git diff --check прошли; оба PHP-патча git apply --check прошли.
PHP executable/MariaDB E2E здесь недоступны. Полный backend suite не заявляется.

На FirstVDS не выполнялись запросы/планы/тесты. Пересчёт полного hash нужен на каждом
GET: возможен повторный scan подразделения, потому что прежний lower(trim(client))
индекс не совпадает с v1 regexp_replace. Новых постоянных индексов и DB-расширений нет.
Нагрузочная ёмкость/VPS не доказаны. При нехватке RAM/тайм-ауте503, не ложный count0;
лимиты кеша/SQL/параллелизма/серверные настройки не повышались. Поток ограничен по
числу headers, не по байтам: очень большие original_products_text требуют отдельной
оценки размера ответа. Администратору следует использовать минимально нужную страницу.

Порядок установки после ревью: проверить фактическую версию PHP, применимость
review patch и E2E fixture, совместно проверить isolated HTTP pipeline с реальным
токеном лаборатории; собрать готовый backend вне VPS, согласованно обновить PHP-
потребителя и backend обычной процедурой. Frontend/БД не меняются. Старый backend
можно вернуть без downgrade; ручная проверка вновь будет недоступна, а не разрешена
по неполному evidence. Авторизацию/mTLS/SHADOW/лимиты automation не отключать.
Никакого автоматического deploy/merge или изменения Calltrack этим PR нет.
