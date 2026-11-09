# Проверяемый переход PostgreSQL 24 → 25 после PR #170

> Историческая инструкция для релиза25. Начиная с контроля импорта default launcher
> ожидает26; для нового релиза используйте [порядок25→26](import_completeness_deployment.md).
> Не применяйте updater/bridge25 к новой схеме26.

Исправление предназначено для `20261022_24` → `20261030_25`. Эти команды — план
отдельной согласованной установки, а не выполненные действия на VPS. Production,
`/var/www/html/vr/update_sales.sh`, volumes и существующие server-only overrides
в ходе разработки не менялись. Новый образ нельзя считать установленным по git SHA.

Реальный production updater512 теперь получен и статически проверен. Рекомендуемый
единый guarded маршрут с явным --migrate-24-to-25 и полным исправленным внешним
артефактом описан в [аудите updater512](update_sales_schema25_updater.md). Он готовит
откат/marker до DDL. Не выполнять отдельную миграцию ниже раньше этого этапа при
работающем launcher24. Сам production-скрипт в Git не добавляется.

## Причина и контракт запуска

PR #170 добавил миграцию `20261030_25_churn_schedule.py` с `down_revision=20261022_24`,
но launcher остался на ревизии 24. Простое расширение SUPPORTED_REVISIONS не решало
проблему default expected revision и явного migration-job.

Теперь `backend/start_backend.py`:

* По умолчанию ожидает **20261030_25**. Проверка схемы при обычном старте и `--check-only`
  работает в `SET TRANSACTION READ ONLY`, без Alembic, stamp, create_all, INSERT/UPDATE/DDL.
  При базе 24 обычный старт отказывает до exec Uvicorn и сообщает обе ревизии.
* Явная команда `--migrate --expected-revision 20261030_25` разрешает только 24 → 25
  или 25 → 25. Для 25 → 25 Alembic вообще не вызывается, но схема проверяется.
  Сохранена отдельная поддержка 23 → 24/24 → 24. Переход 23 → 25 требует двух
  отдельно указанных шагов; понижение 25 → 24 и произвольный `head` запрещены.
* Migration-job требует выключенных analytics flags и SHADOW, удерживает прежний
  SCHEMA_LOCK=734190249 до проверки результата. У конкурирующего job отказ до Alembic.
  Read-only старты берут shared lock. Для postcheck используется **та же** сессия,
  чтобы не ждать собственной exclusive-блокировки на другом соединении.
* Проверяется actual revision после Alembic, физическая схема и при запуске нового
  образа весь текущий ORM-контракт. Успех job выводит `SCHEMA_MIGRATION_OK` и завершает
  процесс; сервер из migration-job не запускается.
* Контракт новых таблиц встроен в mountable launcher независимо от новых ORM-моделей.
  Поэтому при откате в старый образ тоже проверяются все три таблицы ревизии 25.

Новая миграция не изменялась. Она создаёт только `churn_schedule`, `churn_jobs`,
`churn_receipts`, их PK, UNIQUE `uq_churn_job_day_kind` / `uq_churn_receipt_event`,
FK release_id → analytics_automation_releases.id и job_id → churn_jobs.id, а также
служебную sequence для integer PK. Существующие продажи, импорт, каталоги, бюджеты,
подписи, политики и их значения не переписываются. Для `event_id` FK в исходной
миграции **не объявлен** — проверка не выдумывает дополнительное ограничение.

Проверка схемы 25 охватывает каждый столбец, тип и nullable, PK, обе именованные
UNIQUE, обе FK с правильными колонками/таблицами/схемой, отсутствие ON DELETE CASCADE
и других изменённых действий, validated/nondeferrable состояния PK/UNIQUE/FK и
server default false для created_for_release. Историческая совместимость JSONB/JSON
сохранена. Nullability старых таблиц не ужесточается по неточным ORM-подсказкам.

## Ресурсы и исходные условия

По информации оператора: RAM около 1,9 ГБ, свободно около 3,1 ГБ, PostgreSQL около
11 ГБ, проверенная резервная копия находится на компьютере оператора. Эти сведения
не проверялись подключением к production.

* Не делать новый полный dump/restore на этот VPS в рамках установки. Уже имеющаяся
  проверенная копия и понятная точка восстановления должны быть подтверждены оператором;
  учесть изменения после её создания и возможность вернуть их при восстановлении.
* Не требовать 11 ГБ свободного места для этой добавочной DDL-миграции: существующие
  sales/SaleItem не копируются, не агрегируются и не сканируются миграцией 25.
  Она создаёт пустые таблицы/индексы, но небольшой WAL/диск всё равно необходим.
* До операции проверить `df -h`, available RAM, состояние PostgreSQL и место под образ
  и временные Docker layers. Не делать Docker prune и не удалять rollback images/volumes.
  3,1 ГБ сами по себе не доказывают, что новая сборка поместится.
* Предпочтительно собрать и проверить образ вне VPS, для его фактической архитектуры,
  доставить заранее согласованным способом и проверить immutable image ID. Не хранить
  одновременно на VPS ненужную tar-копию и распакованный образ. Если используется
  существующий updater со сборкой, оценить его build-space отдельно до миграции.
* Frontend в этом PR не меняется: его пересборка для исправления launcher не нужна.
  Никаких параллельных тяжёлых сборок.
* Остановить/дождаться текущих импортов и тяжёлых jobs согласованным механизмом.
  Приостановить producer/consumer и оба churn timers, не менять их approved policies
  или бюджеты. SCHEMA_LOCK не заменяет остановку внешних/необёрнутых Alembic-команд.
* SQL job сохраняет прежние lock_timeout=2s / statement_timeout=30s через PGOPTIONS.
  Не увеличивать таймауты при блокировке: выяснить, кто держит ресурс, и повторить
  только после проверки actual revision. Преднамеренные operator PGOPTIONS/URL options
  имеют приоритет и должны быть отдельно проверены.

## Compose и совместимость рабочего update_sales.sh

Получена фактическая копия updater512 (29027 байт, SHA256
`3ecaf732d26a2f7c59bfdfb199c9408c647943ff8554297807494b88ab427f2c`).
Проверка --check-only в ней совместима с candidate25 после миграции, но штатный
image-only rollback несовместим со старым launcher24 на новой БД. Дополнительно при
ошибке после DDL и до up отсутствовал переход в recovery. Исправленный полный
файл предоставляется отдельно, вне Git; [точная установка и тесты](update_sales_schema25_updater.md).
Наличие такой же версии на live VPS, фактические images/overrides и настоящий
production recovery здесь не проверялись. Рабочий файл автоматически не менялся.

До установки оператор должен проверить, что реальный updater:

1. Поддерживает нужный backend-only режим и не скрывает собственный `--migrate`/
   `alembic upgrade head` в обычном обновлении.
2. Использует проверенный launcher нового образа, а не старый bind mount или command
   override с `--expected-revision 20261022_24`.
3. Сохраняет **весь фактический** порядок Compose-файлов: основной, обычный override,
   churn, Calltrack, mTLS и управляемый image override. Не заменять цепочку примером
   из документа без проверки дополнительных server-only файлов.
4. Проверяет imports и схему до переключения, сохраняет предыдущий image и проверяет
   image ID работающего контейнера против effective Compose image.
5. Имеет проверенный путь отката на **схеме25**, изложенный ниже. Простой возврат старого
   image с launcher24 может оставить backend выключенным. Если такой сценарий не
   отрепетирован, штатный автоматический updater использовать для переключения нельзя.

При неизвестном command/mount/ENTRYPOINT или несовпадении effective config и image ID —
STOP и отдельная диагностика. Не выводить `docker compose config`, `.env`, Calltrack JSON,
полный `docker inspect` или environment в чат/общий лог. `config --quiet` допустим.
Данный PR рабочий скрипт и существующие production Compose-файлы не переписывает.

## Низкоуровневые команды и отдельный manual путь
+
+Для production24 с работающим launcher24 основной маршрут — новый
+[guarded updater](update_sales_schema25_updater.md) с подготовкой восстановления
+**до** DDL. Следующие standalone команды служат справочником и изолированной
+репетицией; не применять их к VPS в обход preflight/marker/rollback плана.
+Требуется отдельное разрешение оператора.

### 1. Подготовить candidate и rollback до изменения БД

* Зафиксировать exact source commit, старый working image ID, candidate image ID,
  actual revision 24, проверенный backup и quiescence jobs. Не терять старый образ.
* Убедиться, что candidate содержит этот исправленный launcher и единственный Alembic
  head 20261030_25, чей parent ровно24. Offline `alembic heads` не требует production DB.
* Подготовить отдельную проверенную копию нового launcher вне старого образа; файл
  должен читаться container UID10001, например root-owned0644 без секретов. Старый
  образ не модифицировать. Зафиксировать SHA256 launcher.
* На изолированном стенде проверить **реальный выбранный старый image** с новым
  launcher на25: check-only, maintenance rollback, health/нужные бизнес-API и restart.
  Тесты этого PR проверяют old ORM subset, а не неизвестный production image.
* Подготовить синтаксически проверенный rollback override и всю Compose-цепочку
  **до миграции**, даже если первоначально он не активен.

Дальше `SJ_COMPOSE` обозначает Bash-массив из **уже проверенной полной фактической**
Compose-команды проекта, с прежним env-file и прежним порядком всех серверных файлов.
Ниже нет намеренного сокращения до одного docker-compose.yml. `SALES_CANDIDATE_IMAGE`
и `SALES_ROLLBACK_IMAGE` — заранее проверенные immutable IDs/уникальные reviewed tags,
а `SALES_ROLLBACK_LAUNCHER` — абсолютный путь к отдельно сохранённому launcher25.
Все переменные образов не секретные; private env остаётся только в существующих файлах.

Пример начала массива **только после проверки, что он совпадает с реальным сервером**:

```bash
SJ_COMPOSE=(docker compose --env-file /var/www/html/vr/sales/.env
  -f /var/www/html/vr/sales/docker-compose.yml
  -f /var/www/html/vr/sales/docker-compose.override.yml
  -f /var/www/html/vr/sales/docker-compose.churn-v3u.override.yml
  -f /etc/calltrack/churn-identity/sales-bindings.compose.json
  # Здесь должны остаться остальные реальные mTLS/image override в прежнем порядке.
)
"${SJ_COMPOSE[@]}" config --quiet
```

Это не команда к немедленному выполнению и не полный перечень неизвестных server-only
overrides. Учесть фактическое project name (`-p`), cwd и networks. Добавляемый schema
service должен видеть уже работающую db в прежнем project network; при нестандартной
DB network/credentials его новый opt-in override необходимо отдельно согласовать.

### 2. Отдельно применить точную миграцию 24 → 25

После проверки и отдельного разрешения:

```bash
"${SJ_COMPOSE[@]}" -f /var/www/html/vr/sales/deploy/migrate.churn25.explicit.override.yml \
  --profile schema-maintenance config --quiet
"${SJ_COMPOSE[@]}" -f /var/www/html/vr/sales/deploy/migrate.churn25.explicit.override.yml \
  --profile schema-maintenance run --rm --no-deps schema-migrate
```

`SALES_CANDIDATE_IMAGE` должен быть задан оператором заранее. Service использует только
готовый образ с pull_policy=never, не запускает backend/worker и не пересоздаёт db.
`--rm` касается только собственного одноразового job-контейнера; удаления volumes нет.
Новый opt-in service не включается обычным `compose up`.

Требуется `SCHEMA_MIGRATION_OK expected=20261030_25`. Если job упал, не делать stamp,
downgrade или повторный bootstrap. DDL могла уже закоммититься, а postcheck — обнаружить
несоответствие: сначала read-only проверить реальную revision и причину отказа.
Транзакционная ошибка DDL 25 должна откатить всю миграцию; это тоже проверить фактически.

### 3. Проверить миграцию до переключения backend

Read-only SQL через действующее защищённое подключение `sales-journal-db`:

```sql
SELECT version_num FROM alembic_version;
SELECT to_regclass('churn_schedule'),to_regclass('churn_jobs'),to_regclass('churn_receipts');
SELECT conrelid::regclass,conname,contype,convalidated,condeferrable
FROM pg_constraint
WHERE conrelid IN ('churn_schedule'::regclass,'churn_jobs'::regclass,'churn_receipts'::regclass)
ORDER BY conrelid::regclass::text,conname;
SELECT id,revision,approved,enabled FROM churn_schedule;
```

После первой миграции25 `churn_schedule` пустая: миграция не создаёт enabled/approved
policy и не запускает постоянный сценарий. Если25 уже использовалась, существующие
утверждённые данные не выключаются и не перезаписываются повторным job; их состояние
проверяется отдельно оператором. Внешние timers не включаются этим PR.

Для полной проверки запустить candidate только с `--check-only`, без lifespan:

```bash
# Тот же одноразовый service и ready image, но команда переопределена read-only:
"${SJ_COMPOSE[@]}" -f /var/www/html/vr/sales/deploy/migrate.churn25.explicit.override.yml \
  --profile schema-maintenance run --rm --no-deps schema-migrate \
  python -B start_backend.py --check-only --expected-revision 20261030_25
```

Требуется `SCHEMA_PREFLIGHT_OK expected=20261030_25`. Этот check использует все модели
candidate и независимый контракт новых таблиц, не ограничивается тремя to_regclass.

### 4. Обновить только backend image и проверить здоровье

Только после предыдущих проверок и подтверждения совместимости реального updater:

```bash
# Только исправленный updater; схема25 и rollback25 уже отдельно подтверждены:
/var/www/html/vr/update_sales.sh --backend-only
```

Если updater пересобирает образ, не считать заранее протестированный candidate тем же
образом автоматически: сверить исходники и фактический built image ID, повторить
preflight именно для него. Если места для сборки/rollback нет — STOP, не чистить VPS.
Порядок и все параметры существующей безопасной процедуры переключения сохраняются.

После переключения: `curl -fsS http://127.0.0.1:8010/api/health`, затем защищённая проверка
`/api/settings/churn-scenario/status` и `/automation` без токенов/паролей в командной
строке или общих логах. Проверить container running/health и его `.Image` против
проверенного effective Compose image ID; не печатать полный inspect/env.

Frontend не изменялся; если он всё же обновляется отдельным согласованным релизом,
проверить `http://127.0.0.1:8011/vr/sales/`. Сбой frontend не требует отката здорового
backend или destructive DB rollback. Постоянную автоматизацию не включать вместе
с техническим исправлением миграционного launcher.

## Откат к старому образу на сохранённой25

Нельзя понижать Alembic revision, делать stamp24, удалять новые таблицы или возвращать
11-ГБ backup ради обычного отката Python-кода. Старый штатный launcher24 на25 не
подходит. Использовать именно подготовленный, протестированный launcher25:

```bash
"${SJ_COMPOSE[@]}" -f /var/www/html/vr/sales/deploy/rollback.churn25.checked.override.yml \
  config --quiet
"${SJ_COMPOSE[@]}" -f /var/www/html/vr/sales/deploy/rollback.churn25.checked.override.yml \
  run --rm --no-deps backend python -B /run/sales-rollback/start_backend.py \
  --rollback --check-only --expected-revision 20261030_25
# После успешной отдельной проверки и разрешения переключения:
"${SJ_COMPOSE[@]}" -f /var/www/html/vr/sales/deploy/rollback.churn25.checked.override.yml \
  up -d --no-deps --no-build --pull never backend
```

Для старого образа с нестандартным ENTRYPOINT отдельно проверить, что он не вызывает
Alembic до переданной команды. Если вызывает — STOP и отдельный reviewed entrypoint
override; не обходить его наугад. Launcher загружает **старые модели из /app**, не из
нового checkout, но также проверяет все три новые таблицы по замороженному контракту.

Это **maintenance rollback**: lifespan OFF, аналитические флаги OFF. Старые фоновые
FTP/scenario workers не стартуют; HTTP-функции требуют проверки конкретного образа.
Автоматическое создание задач не возобновляется. Нельзя выдавать этот режим за полную
работу всех старых фоновых задач.

После отката проверить health, image ID, revision25, сохранность новых таблиц и
ошибок запуска. Держать rollback override в фактической активной Compose-цепочке,
пока rollback работает: другой вызов без него может выбрать иной image/CMD. Не
оставлять effective YAML на candidate, когда реально работает old image, и наоборот.
Отключить обычный updater до отдельного согласования выхода из maintenance rollback.
При ошибке отката сохранить образы, launcher, overrides и диагностику; ничего не удалять.

## Проверки разработки и ограничения доказательства

Локальный изолированный PostgreSQL **16.15**, синтетическая БД
`analytics_shadow_test` на localhost, отдельная случайная schema для каждого теста.
Реальные Alembic migrations с пустой базы до24 и реальный subprocess 24 → 25,
без production URL, продаж, API или нагрузочных запросов. Контейнер PG ограничен
256 MiB и одним CPU; VPS не использовался.

`tests/test_schema25_startup.py` проверяет граф и allowlist, default25, отсутствие
миграции/exec при ошибках, flags, 24 → 25, 25 → 25 без Alembic, сохранность всех
исходных синтетических таблиц/политик, read-only preflight, old-model compatibility,
missing tables/columns, неверные типы/nullability/PK/обе UNIQUE/обе FK/default,
неизвестную/ветвящуюся revision, NOT VALID/DEFERRABLE/CASCADE FK и FK в чужую schema,
конкурирующий migration-job и реальный CLI с контролируемой exec-заглушкой Uvicorn.

Последний тест проверяет launcher→exec, а не запускает настоящий production backend.
Точный старый production image, рабочий update_sales.sh, server-only Compose и
health бизнес-API на них в Codex недоступны. Их репетиция — обязательный операторский
этап до миграции. Результаты доступных прогонов приводятся в PR; не подменять ими
успешный production deploy или утверждение постоянной автоматизации.

Результат полного backend-прогона: **1002 passed, 46 skipped**, включая 34 новых теста
этого исправления (21 с настоящим PostgreSQL16.15 и 2 с Docker Compose config). Отдельно
выполнены Python compile и `git diff --check`. Ранее существующий Starlette/AnyIO
DeprecationWarning не относится к миграции. Пропуски — другие opt-in integration
стенды; они не выдаются за проверенные здесь.
