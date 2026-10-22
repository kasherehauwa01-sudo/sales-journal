# CHURN_RISK: повторный технический аудит 22.10.2026

## Область доказательств

Только Codex: `/workspace/sales-journal-integration`, `/workspace/calltrack-verification`. Production не запрашивался и не изменялся. Реальные customer datasets не использованы. Тестовые продажи синтетические; PostgreSQL, MariaDB, транзакции, HTTP и PHP — настоящие.

Проверены новые upstream версии: Sales `4b156298daf313621eb9e13242d3122063107d6c` (PR156), Calltrack `185bc0a4f16d771327fc9365c280e5bcc276ae6f` (PR504). Их деревья точно совпадают с ранее подготовленными `59674c7` / `c05b6de`. Sales reported production `de0a0c8` — ancestor main; Calltrack `530adc1` — ancestor integration branch. Fast-forward Git не доказывает совпадение запущенного image, отсутствие server-only файлов или безопасную миграцию.

Исправления этого аудита находятся в отдельных ветках `audit/churn-risk-production-readiness`: база Sales main, база Calltrack feat/analytics-manager-bindings. Main и production-ветки не изменялись.

## Вердикты

| Компонент | Статус | Предел вывода |
|---|---|---|
| Формулы INACTIVITY, scope Авиаторов / buyer types policy /2 | PASS | Регрессии, формулы не изменены |
| STORED_SALES scan/resume/supersede/revision | PASS | Автономные и PostgreSQL tests; UNKNOWN остаётся UNKNOWN |
| Producer defaults, signed manifest, policy, identities, immutable release | PASS | Изолированная БД и HTTP, отрицательные доказательства |
| Consumer policy, assignments, budget, concurrent idempotency | PASS после исправления | Основной маршрут + direct-service regression |
| HTTP failure / partial receipt / replay / DONE | PASS | Инъекции ошибок в настоящий transport и MariaDB |
| Новые DDL upgrade/downgrade / MariaDB SQL | PASS на стенде | Не проверка server privileges, disk и полного production schema |
| Реальные источники: полнота, свежесть, stable IDs, managers | BLOCKED | Нет независимых production доказательств |
| Сохранение server-only функций / TLS / cron / runtime образ | BLOCKED | Только сведения владельца, свежая серверная сверка не выполнена |
| Восстановление actual production / запас диска | BLOCKED | Нет проверенного восстановления; 4.5 ГБ свободного места не гарантируют build/migration |
| Установка disabled | BLOCKED | Требуются recovery/preflight и отдельное разрешение |
| Активация и реальные задачи | BLOCKED | Все source/business gates и новое явное разрешение |

PASS означает конкретный проверенный технический сценарий, не общую безопасность production.

## Найденные проблемы

1. **HIGH, исправлено:** `Calltrack/api/manager_tasks_service.php:createManagerTask` доверял внутреннему `automatic.allowed=true`, проверяя лишь budget. Прямой вызов мог создать автоматическую задачу при неутверждённой business policy, если budget отдельно утверждён. HTTP manager_tasks API этого аргумента не принимает; основной processAnalyticsEvent уже проверял policy. Теперь сама primitive в транзакции блокирует/перечитывает DB policy и требует enabled CHURN_RISK, approved ACTIVE envelope и совпадение stable client/manager assignment. Регрессии: approved budget + unapproved policy, disabled type, нулевые tasks/counter, обычный replay/rollback/manual lifecycle. Это защита от ошибочного внутреннего вызова, не sandbox против произвольного кода с DB-доступом. Полный freshness/identity gate остаётся в processAnalyticsEvent.
2. **MEDIUM, исправлено:** Python equality допускал `confirm_business_approval: 1` вместо literal true; revision True/float мог сравниваться равным integer CAS. `analytics_automation.py:manage_policy/review_identity` теперь требует nonnegative exact int revision; approve — exact JSON true и единственное поле. Неправильные значения отвергаются до записи. Отдельные regression cases в management test.
3. **HIGH, эксплуатационный BLOCKED:** предыдущий runbook предлагал новый backup; пользователь теперь отказался. Новые backup не создаются и не предлагаются к автоматическому выполнению. Отсутствие проверенного восстановления блокирует B и D. Нельзя считать отказ разрешением на DDL без восстановления.
4. **HIGH, эксплуатационный BLOCKED:** старый backend Alembic23 не знает revision24; стандартный CMD старого image может не стартовать после upgrade. Нужен отдельно reviewed override uvicorn без Alembic и rehearsal именно сохранённого старого production image. Никаких stamp23/downgrade/drop для обхода.
5. **Уточнения задания:** migration24 создаёт ПЯТЬ таблиц, включая analytics_automation_decisions; `downgrade()` существует и удаляет их. Он destructive и не штатный production rollback.

## Анализ критических путей

Producer: SOURCE_LOCK serializes cooperating imports/worker/release; policy FOR UPDATE + CAS; immutable UNIQUE source_run_id/sequence. Missing feature/policy/key/signature/source set/completeness/client freshness => no release. Manifest binds as_of, last closed Moscow day, source_revision, exact frozen scope/facts/identities. Canonical actual facts hash streams in batches; expected proof обязан исходить независимо из upstream, не self-attested local digest. Signed claims не доказывают их истинность, ключ HMAC позволяет и подписывать: организационное разделение signer и app permissions обязательно.

Sales CLI preview не сохраняет новые записи, но берёт locks и может блокировать импорт; это НЕ строгая production read-only операция. release сохраняет decisions даже при BLOCKED, а при успехе separate releases/events. policy-draft/approve/revoke/identity-review пишут PostgreSQL и audit. Их запуск на production не разрешён.

Raw worker: читает реальные stored sales с PK watermark, scope/date, положительными purchase days; не полагается на MAX(date) для completeness. Последний закрытый день — ограничитель даты, не evidence полноты. Resume сохраняет frozen as_of; изменение scope/revision приводит к SUPERSEDED без потери прошлой опубликованной версии. Неполный импорт/ClientsVR unavailable блокируют продолжение. SHADOW пишет собственные run/features/evaluations/signals/publications/state, поэтому не является отсутствием DB writes. SOURCE_LOCK/скан/I/O могут влиять на latency обычного импорта. Production нагрузка и большой dataset не тестировались.

Calltrack пути создания:

- `manager_tasks.php` -> createManagerTask: authenticated admin + CSRF, ручная задача, не зависит от automation. Менеджер не может сам назначать; supervisor read-only.
- `analytics_sync.php` CLI -> syncAnalytics -> processAnalyticsEvent -> createManagerTask: mode disabled => no requests/writes; dry_run сохраняет inbox/diagnostics/cursors/cache metadata, но не tasks; enabled требует producer approval, current source/client evidence, stable binding, manager mapping, DB business policy и budget.
- Прямой trusted PHP вызов processAnalyticsEvent подчиняется этим же gates; нижняя primitive теперь повторяет persisted policy/assignment guard. Она не является отдельным публичным автоматизирующим API. Не передавать ей непроверенное allowed=true из будущих маршрутов.

Budget и task/slot/inbox обновляются в одной транзакции; lifetime counter не обнуляется. Уникальные signal key и open slot + row locks ограничивают дубли; конкурирующая транзакция может получить deadlock/duplicate-key и должна повториться без потери cursor. Transport at-least-once, не обещание exactly-once HTTP. Existing DONE не переоткрывается, manager changes => REVIEW.

Изменение producer policy/source/binding запрещает последующую выдачу с 410; shared transaction между PostgreSQL и MariaDB отсутствует. Уже полученный event может успеть обработаться до следующей проверки producer revocation, пока его proof/local source evidence не истекли. Немедленная глобальная отмена всех in-flight событий не гарантирована. Для остановки закрывать локальную consumer DB policy/budget и останавливать consumer; дождаться transaction completion. Это требует отдельного разрешения. Уже созданные задачи автоматически не исчезают.

Bearer/HTTPS защищают transport; HMAC проверяется producer. Consumer не верифицирует upstream HMAC самостоятельно: доверяет authenticated Sales endpoint плюс собственной независимой source evidence. Реальный TLS/proxy/token provisioning production не проверен.

## Миграции

PostgreSQL `20261020_23 -> 20261022_24`: policy, identities, releases, events, decisions; FK к analytics_signal_runs/release; UNIQUE source_run_id/sequence; ix_automation_release_event(release_id,id). PostgreSQL transactional DDL проверен на новой test schema, roundtrip не изменяет старые shadow tables. New tables runtime не создаёт. Неизвестные существующие объекты/partial state => STOP, не overwrite.

MariaDB: прежние manager_bindings / analytics_tasks / analytics_task_policy уже reported присутствуют; сравнить metadata/constraints, не повторять SQL. Только `database/20261022_analytics_budget.sql` для полностью отсутствующих трёх новых таблиц. InnoDB, closed defaults, revision/cursor separation. DDL implicit commits: ошибка может оставить часть таблиц/seed rows, script не идемпотентен. Ни --force, ни повтор вслепую. Сохранить приватный журнал ошибки и сверить metadata; repair только отдельным reviewed планом. Code rollback оставляет audit/used_tasks/cursors, не DROP.

## Этап A — только сверка, разрешённая владельцем

Команды ниже подготовлены, на production НЕ выполнены. Вставлять по одной в server Bash, SQL — только внутри psql. Полные bounded read-only PG/MySQL/SQLite блоки: Calltrack `docs/churn-risk-production-readonly.md`. Не выполнять app bootstrap/ensure/refresh/sync/preview ради диагностики.

```sh
git --no-optional-locks -C /var/www/html/vr/sales rev-parse HEAD
```

```sh
git --no-optional-locks -C /var/www/html/vr/sales status --short --branch
```

```sh
git --no-optional-locks -C /var/www/html/vr/calltrack rev-parse HEAD
```

```sh
git --no-optional-locks -C /var/www/html/vr/calltrack status --short --branch
```

```sh
docker inspect --type container --format 'image={{.Image}} ref={{.Config.Image}} status={{.State.Status}}' sales-journal-backend
```

```sh
df -h / /var/lib/docker /var/lib/containerd
```

```sh
docker system df
```

Не запускать prune/удаление файлов/образов. Не печатать Docker Env или private config. Получить read-only runtime flags, schema23/expected tables/indices, import statuses и source revision по отдельному документу; сверить cron/PHP-FPM env приватно. Проверить существующие recovery artifacts/процедуру, сроки/RPO и rehearsal без создания нового production backup. Диск 4.5 ГБ => STOP, пока не доказан запас под новый image + temporary build + DB/WAL; не угадывать размер и не освобождать место без разрешения.

## Этап B — установка disabled, пока BLOCKED

Нет разрешения на выполнение команд этого раздела. Ниже точные формы будущих команд, а не готовый скрипт: `${APPROVED_COMPOSE}`, `${REVIEWED_IMAGE}`, `${MIGRATION_NETWORK}`, `${PRIVATE_DB_ENV}`, `${PRIVATE_MYSQL_DEFAULTS}`, `${CALLTRACK_RELEASE}` должны быть зафиксированы владельцем по A в private run sheet. При пустом/непроверенном параметре STOP. Нельзя подставить случайный network/env или грязный server checkout. Новые audit commits сначала отдельно review/merge владельцем; PR не deploy.

1. Gate: отдельное разрешение B, existing recovery доказан, rollback old image/schema24 отрепетирован на изолированном стенде, диск достаточен, code/server diff принят. Иначе никаких команд B.
2. Scheduler/flags сохраняются disabled; consumer policy unapproved, producer key empty. No new backup по текущему решению. Никаких запусков аналитики.
3. Собрать reviewed clean tracked context вне production checkout, не включая .env/untracked backups/data. Точное содержимое и SHA утверждены; command (после выделения места):

```sh
docker build --tag "$REVIEWED_IMAGE" "$REVIEWED_BACKEND_CONTEXT"
```

4. Проверить единственную Alembic head без подключения к БД:

```sh
docker run --rm --network none --entrypoint alembic "$REVIEWED_IMAGE" heads
```

Ожидается только 20261022_24. Другая head => STOP. ENTRYPOINT/CMD заранее сверены; обычный запуск backend здесь запрещён.

5. Одноразовая разрешённая PostgreSQL миграция к точной цели; private env только DB/settings, flags disabled, approved network и image:

```sh
docker run --rm --network "$MIGRATION_NETWORK" --env-file "$PRIVATE_DB_ENV" --entrypoint alembic "$REVIEWED_IMAGE" upgrade 20261022_24
```

6. Read-only metadata verification пяти таблиц/FK/UNIQUE/index/policy; ошибка => STOP, backend не стартовать.
7. Только если новые MariaDB таблицы полностью отсутствуют и старые verified:

```sh
mariadb --defaults-extra-file="$PRIVATE_MYSQL_DEFAULTS" calltrack < "$CALLTRACK_RELEASE/database/20261022_analytics_budget.sql"
```

Нет --force; любой partial результат => STOP. Credentials-file согласован, не создаётся/не печатается этим заданием.
8. Disabled Sales launch исключительно reviewed compose release/override с pinned image; services db/frontend/worker не пересоздаются:

```sh
docker compose -f "$APPROVED_COMPOSE" up -d --no-deps --no-build backend
```

CMD автоматически Alembic: допустим только после отдельной schema24 проверки; head must remain24. Calltrack release switch зависит от фактического PHP deployment, которого мы не знаем: STOP до отдельной точной команды владельца, не rsync поверх untracked и не blind pull/reset. Это явный нерешённый execution binding, не готовность к deploy.

## Этап C — только read-only после согласованной установки

Не запускать CLI sync/preview/release/worker, даже dry-run пишет diagnostics или locks. Metadata/SQL read-only с timeout, flags masked. Проверить revision24 и closed producer row; Calltrack approved0 / CHURN enabled0 / budget NULL,0,0 / separate cursor0. До/после task count и source-specific task/receipt counts сравнить с baseline; для больших таблиц только indexed/bounded проверка по согласованному диапазону времени, не тяжелый fullCOUNT. Unexpected task/event/counter movement => STOP и расследование, не очистка.

Образ должен совпасть с утверждённым SHA, schema с миграцией. HTTP health/logging может писать access audit: это отдельная smoke-проверка только при разрешении, не строгая read-only стадия C. Не заявлять UI/production E2E пройденным.

## Этап D — ограниченное включение, отдельно BLOCKED

До нового явного разрешения ничего не включать. Требуются independent source registry/complete-through/count+digest/current revision; настоящий manifest provider и signer separation; полная reviewed client history/aliases; active Calltrack manager mapping; producer/consumer min confidence; priority, due_days/timezone; proof/source/client/signal TTL; lifetime budget и ответственный; расписания/скорость; stop thresholds; replay/re-evaluation и приемка первой партии. 0.65/HIGH/1 день/10 задач не утверждены. PURCHASE_OVERDUE не включать.

Сначала отдельно разрешённый preview с ограниченным scope и private review (locks!), затем конкретный release и consumer pilot с cap. Это запись/создание задач, в текущей задаче запрещено. Нельзя объявить UNKNOWN истинным по MAX(date), successful import или локальной подписи. Реальные completeness/identity/manager evidence сейчас BLOCKED.

## Откат

Не выполнять сейчас. В согласованном окне: остановка consumer/release, локальная policy/budget revoke, ожидание in-flight transaction, private incident review. Сохранить committed tasks/results/history/inbox/budget/cursors. Вернуть old Calltrack disabled; old Sales image с заранее отрепетированным uvicorn-only launch (без Alembic). Additive schema24 сохраняется. Никаких downgrade/stamp23/cursor reset или used_tasks reset. Необратимый DDL/потеря данных => только проверенная existing recovery процедура; её отсутствие блокирует B и D, отказ от нового backup не снимает gate.

## Тесты и ограничения

Точные команды и результаты повторного запуска находятся в companion `churn-risk-test-results-20261022.md`. Проверяются реальные синтетические сценарии, не production. Не проверены server filesystem/image, actual config/scheduler, TLS, полномочия DDL, свободное место/WAL/время блокировок на 9.54ГБ, восстановление production, физическое kill/power loss/диск-full и реальные source/identity/manager claims. Инъекция ошибки receipt проверяет транзакционную границу, не все типы аварий.

**Решение:** технические тесты разрешают дальнейшее review исправлений; production disabled deployment пока BLOCKED до preflight/recovery/permission. Реальные CHURN_RISK задачи отдельно BLOCKED.
