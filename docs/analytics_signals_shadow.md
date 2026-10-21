# INACTIVITY — только Shadow

## Границы

Первый движок генерирует только PURCHASE_OVERDUE и CHURN_RISK как стадии одного
INACTIVITY episode. Он не обращается в Calltrack/ClientsVR, не создаёт задачи,
уведомления, звонки, не меняет продажи или назначения менеджеров. HTTP feed не
реализован. `mode=SHADOW` закреплён конфигурацией и CHECK в БД;
`automation_allowed=false` всегда. Это не выключенная реализация задач: такой
реализации здесь нет.

Worker default disabled в ENV (`ANALYTICS_SIGNALS_ENABLED=false`) и durable
singleton state (`enabled=false`, отсутствие строки также означает disabled).
Web lifespan worker не запускает. Просмотр отчёта не запускает расчёт.

## Идентичность и совместимость

Продуктовый MVP-контракт: `identity_kind=normalized_name`, версия 1, имя — основной
ключ, телефон — evidence. Trim + схлопывание явного Unicode whitespace alphabet
+ lower, без перестановки слов, fuzzy matching и замены ё на е. SQL PostgreSQL —
авторитетное вычисление persisted key с текущей DB collation. Поддержка имени в
Python — versioned path существующего `normalize_client_name`; legacy default
casefold и старые SQL keys остаются неизменными. SQL/Python parity проверяется на
русских/латинских именах и Unicode spaces; перед другой collation/сменой версии
нужны shadow comparison tests. Произвольный Unicode case mapping PostgreSQL и
Python может отличаться, поэтому внешние потребители используют опубликованный
key, а не строят его самостоятельно.

RFM/client-insights/buyers/dashboard/categories сохраняют контракты и старые
ключи. В RFM выделен shared `interval_statistics` для legacy mean между distinct
dates; старые RFM purchase days по всем чекам не подменяются positive-only молча.
Shadow SQL feature provider реализует ту же математическую статистику по явному
положительному purchase-event scope. Средний чек = SUM total_amount / COUNT checks
за 365 дней, reference receipts = SUM total_amount за 180 дней. Эти денежные
агрегаты используют имеющиеся net stored amounts без join с sale_items.

Одинаковые имена намеренно объединяются для аналитики, включая несколько
телефонов. Разные имена с одним телефоном не объединяются. Это может объединить
одноимённые компании или разделить переименованного клиента; GUID не blocker
shadow MVP. Будущий Calltrack самостоятельно проверяет свою карточку; ambiguous
matching не создаёт задачу. В будущем identity_kind/external_id + новый feature
key provider позволят использовать stable IDs без изменения evaluator. Старые
эпизоды не перепривязывать молча: нужен versioned migration/reconciliation plan.

Хранятся максимум 10 последних наблюдавшихся нормализованных телефонов на имя
(по last_seen DESC,phone). Набор ограничен в БД и JSON. При десяти элементах
`phones_may_be_truncated=true` консервативно. Diagnostics multiple_phones считает
клиентов с несколькими observed phones, не считает их ошибочными. Короткие/неполные
телефоны не evidence. Клиент без телефона не исключается.

## Даты, источник и freshness

`Sale.sale_date` — DATE из документа: не timestamp, UTC-конвертации нет.
Timezone берётся из существующего AUTOLOAD_TIMEZONE (default Europe/Moscow).
Default as_of = вчера по этому timezone; будущий/текущий день запрещён.
`--as-of` позволяет явный закрытый исторический день. as_of и его основание
сохраняются в run. Backwards относительно опубликованного среза запуск запрещён.

Manifest отсутствует, поэтому persisted `coverage_status=UNKNOWN`,
`complete_through=null` всегда. UNKNOWN разрешает shadow; не означает достоверную
полноту. MAX(sale_date) не используется для полноты или выбора as_of.

Отдельный operational freshness:

* HEALTHY: последний batch completed, error_rows=0, finished_at не старше
  ANALYTICS_SIGNALS_FRESHNESS_HOURS (36 default), нет queued/processing;
* UNHEALTHY: pending batch или последний импорт failed/с row errors;
* STALE: нет свежего завершённого импорта.

Это проверка доставки, не полного покрытия. Давняя ошибка импорта не исчезает
как факт из истории из-за следующего успешного batch; нынешняя policy не
доказывает её устранения, coverage остаётся UNKNOWN. Для исторического as_of
freshness относится к состоянию импортов на момент запуска, что явно сохранено.
При STALE/UNHEALTHY новые эпизоды не создаются, существующие SUSPENDED с
SOURCE_UNRELIABLE. Никакого ложного PURCHASE_OCCURRED. Будущая automation должна
проверять подтверждённый coverage отдельно; этот worker её не предоставляет.

## Общие признаки

В 365-дневном закрытом окне (as_of-364 … as_of) используется distinct sale_date
с хотя бы одним total_amount>0. Несколько чеков дня не создают gap=0. Нулевые и
отрицательные чеки не являются новой квалифицирующей покупкой, но сохранённые
суммы учитываются в денежных aggregates. Сертификаты уже вычтены импортёром;
выдача с нулевым net amount поэтому не сбрасывает inactivity. Возвратные префиксы
уже фильтрует существующий importer; новый алгоритм не восстанавливает отсутствующие
возвраты и не претендует на бухгалтерский net turnover.

Feature provider: mean, median, Q25/Q75 (continuous/inclusive interpolation),
MAD=median(abs(gap-median)), gap_count, distinct_purchase_days, window history span,
lifetime first/last positive purchase, days_without_purchase, checks/revenue/average
check за 365 дней, receipts за 180 дней, phones/display name. Cycle статистика
считается SQL LAG + percentile_cont, не массивом истории в Python. RFM mean
остался backward compatible и проверяется теми же определениями/compatibility suite.

Eligibility: >=8 purchase days, >=7 gaps, span>=56, median>0;
MAD/max(median,1)<=0.75; median<=60. Reasons: ELIGIBLE,
INSUFFICIENT_HISTORY, IRREGULAR_PURCHASE_PATTERN, INTERVAL_TOO_LONG,
NO_VALID_PURCHASE_DAYS, SOURCE_UNRELIABLE. Нет default interval=30.

overdue = max(ceil(1.5*I),ceil(Q75)+2,ceil(I)+2).
churn = max(ceil(3*I),ceil(Q75)+7,14).
T>=churn → CHURN_RISK, иначе T>=overdue → PURCHASE_OVERDUE, иначе NORMAL.

Confidence quality/1: .35*min(gaps/20,1)+.35*max(0,1-D)
+ .20*C + .10*min(span/180,1). Для UNKNOWN C=0. Это quality index, не probability;
ни один confidence score не обходят hard eligibility failures. В shadow нет
дополнительного confidence cutoff: ручной фильтр отчёта позволяет оценить выборку.

Commercial/1: s=clamp((T/I-1)/2,0,1); daily=max(receipts_180,0)/180;
reference_revenue=daily*28; loss=daily*max(T-I,0);
v=clamp(log1p(reference_revenue)/log1p(300000),0,1);
l=clamp(loss/30000,0,1); score=round(100*(.5s+.3v+.2l),2).
Raw receipts сохраняются даже при отрицательном результате, score clipping явный.
Constants, coverage contribution, versions и provenance сохранены в metrics.

## Lifecycle и публикация

Один открытый (ACTIVE/SUSPENDED) INACTIVITY episode на kind/key/version.
PURCHASE_OVERDUE→CHURN_RISK сохраняет id, episode_no, повышает revision и создаёт
ESCALATED audit event. Первый срез сразу выше churn создаёт CHURN_RISK.
Новая positive purchase позже anchor закрывает старый episode с PURCHASE_OCCURRED;
если последующая пауза уже due, создаётся следующий episode с новым UUID и номером.
Изменение данных/модели не называется покупкой: DATA_CORRECTION,
MODEL_REEVALUATION, структурированный eligibility reason/SOURCE_UNRELIABLE.
Старые клиенты остаются в diagnostics даже после удаления продаж или выхода из
окна. Потеря eligibility приводит к SUSPENDED, а не к исчезновению.

CREATED/UPDATED/ESCALATED/DEESCALATED/SUSPENDED/RESUMED/RESOLVED записываются
транзакционно вместе с signal revision и checkpoint. Повторный осмотр без изменения
публичного результата меняет last_evaluated_at, но не audit revision. Новый as_of
с изменением давности — meaningful UPDATED, не новый episode. Bigserial audit id
не является готовым Calltrack change-feed cursor: будущему feed нужен отдельно
commit-ordered publisher/epoch, как описано в аудите.

## Worker

Подготовка и ручное включение **только в выбранной изолированной/одобренной среде**:

```bash
cd /workspace/sales-journal/backend
# DATABASE_URL должен указывать на нужную отдельную БД.
../.venv/bin/alembic upgrade head
ANALYTICS_SIGNALS_ENABLED=true ../.venv/bin/python -m app.analytics_worker enable
ANALYTICS_SIGNALS_ENABLED=true ../.venv/bin/python -m app.analytics_worker run
../.venv/bin/python -m app.analytics_worker status
../.venv/bin/python -m app.analytics_worker pause
```

`worker` вместо `run` — standalone polling process. Настройки --scan-size 2000
(max5000), --client-size 250(max1000), --pause .25, --poll60; --as-of YYYY-MM-DD.
SIGINT/SIGTERM останавливает после текущей транзакции. State выключен по умолчанию;
ENV gate и durable enabled оба необходимы. `pause` отключает следующий batch,
checkpoint остаётся. `status` не создаёт state. Enable не запускает расчёт.

Один session advisory WORKER_LOCK; source lock берётся кратко на один bounded
batch, проверяется перед COMMIT вместе с leadership lock. Statement timeout 30s,
lock timeout 2s, memory headroom по существующему runtime. Scan идёт по sales PK
с upper_id; checkpoint и additive upsert daily rows коммитятся вместе, повторного
сложения после crash нет. Смена source_revision во время scan → SUPERSEDED,
неполный input не публикуется. Следующий запуск начинает новый input snapshot.
Evaluate работает по уже зафиксированным derived facts; более поздние изменения
источника помечают отчёт stale и будут учтены следующим run. Worker не изменяет
импортёры/исходные продажи. Ошибка batch откатывается, error_count/code сохраняются;
restart резюмирует FAILED run с тем же as_of и версией. Во время активного run не
менять его as_of/algorithm version.

Каждый новый дневной срез этого первого MVP делает полный bounded PK build:
это НЕ incremental CDC. All-history daily projection нужна для lifetime first/last;
это client/day receipts, а не копия чеков/items. Нормализация применяется только к
ограниченному PK batch, поэтому нет нового непроверенного expression index sales.
Evaluation keyset по клиенту; feature/phone/signal queries set-based. HTTP report
читает только frozen evaluations последнего SUCCEEDED run, не частичную новую
генерацию. Старый отчёт доступен во время pause/failure. Mutable episodes/history
обновляются batch-ами в shadow; перед будущим внешним feed нужен отдельный
publication protocol. Версии/identity namespace подготовлены, внешний feed отсутствует.

## Диагностический отчёт

`GET /api/reports/analytics-signals` требует существующий settings-admin session.
Те же права у UI «Отчёты → Сигналы · Shadow». GET only, без start/enable endpoints.
Фильтры search,signal_type,status,eligible,reason,min_score,min_confidence;
page_size<=100. Keyset `next_after`/`after`, cursor закреплён за run+filters;
сменился run →409 и надо начать с первой страницы. ACTIVE first, score DESC,key.
Нет загрузки всей популяции/истории в браузер и большого OFFSET.

Summary: processed/eligible/not_eligible, counts по reasons/types/status,
missing_empty_name (строки без имени, это НЕ число неизвестных клиентов),
multiple_phones, scanned_sales, transition counts, duration, error_count,
as_of/freshness/UNKNOWN coverage. Missing names не превращаются в фиктивного клиента.
На этом этапе retained run evaluations/daily projections не удаляются автоматически;
для длительного пилота нужен согласованный budget/retention и bounded cleanup.
Не включать бесконечный daily full build на production без измерений/решения.

## Проверки

`ANALYTICS_SHADOW_TEST_DATABASE_URL` — только localhost `/analytics_shadow_test`;
PostgreSQL tests создают/удаляют собственную schema, не используют app DATABASE_URL.
Unit/golden cases, SQL/Python feature parity, PK batch resume/rollback,
same-asof idempotency, transition lifecycle/new episode, no-phone/multi-phone,
same-phone/different-name, UNKNOWN/freshness, protected GET report, keyset paging,
constant-query feature batches. Existing RFM and Calltrack suites обязательны;
никакие Calltrack source files или production services не меняются.
