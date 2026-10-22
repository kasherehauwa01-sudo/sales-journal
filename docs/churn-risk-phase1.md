# CHURN_RISK — первый этап разрешённой автоматизации

Дата проверки: 22.10.2026. Рабочая ветка обоих проектов: `feat/churn-risk-automation-phase1`. Разработка выполнена локально без production-действий. Публикация изменений через GitHub — отдельный подготовительный этап; merge/deploy и включение production требуют отдельного разрешения.

## Решение

**Технический критерий изолированного E2E выполнен:** реальные синтетические продажи в PostgreSQL → штатный INACTIVITY worker → штатное разрешение CHURN_RISK → сохранение отдельной публикации → настоящий HTTP → Calltrack + MariaDB → ровно одна задача правильному менеджеру при конкурентной обработке → повтор без дубля → DONE и история.

Это не mock-адаптер БД и не переписанный тестом AUTOMATION_APPROVED-конверт. Только внешний ClientsVR и независимый upstream-подписант представлены синтетическими fixtures. PostgreSQL/MariaDB, worker scan/evaluate/reconcile, release-транзакции, HTTP/Bearer и Calltrack task service настоящие.

**Production остаётся BLOCKED.** Нет утверждённых порогов/сроков/лимита, реальных проверенных bindings и независимого подтверждения обязательных upstream sources. Реальный source completeness не установлен. Стенд не является разрешением внедрения.

## Что реализовано

### 1. Producer и отдельная неизменяемая лента

Исторические SHADOW-сигналы и публикации не меняются, исходный `ANALYTICS_SIGNALS_MODE` по-прежнему Literal SHADOW. Новый `ANALYTICS_AUTOMATION_ENABLED` по умолчанию false; `ANALYTICS_AUTOMATION_MANIFEST_KEY` по умолчанию пуст. Ни Docker Compose, ни production feature flags не менялись.

Операторский модуль `python -m app.analytics_automation` предоставляет:

- `policy-draft --input PRIVATE_JSON --actor REVIEWER --revision N`: сохраняет только явно заданные параметры и отзывает допуск.
- `policy-approve --input PRIVATE_CONFIRM_JSON --actor REVIEWER --revision N`: отдельное явное подтверждение бизнес-политики; неполная политика не утверждается.
- `policy-revoke --actor REVIEWER --revision N`: выключает допуск, повышает revision, не удаляет историю.
- `identity-review --input PRIVATE_IDENTITY_JSON --actor REVIEWER --revision N`: подтверждение/отзыв устойчивой идентичности, manager_id и всей истории, с CAS и аудитом.
- `preview --run-id RUN_UUID --input PRIVATE_SIGNED_MANIFEST`: только читает и возвращает причины/счётчики; не создаёт release/events/tasks.
- `release --run-id RUN_UUID --input PRIVATE_SIGNED_MANIFEST`: повторяет все проверки, записывает решения и отдельную immutable release/events в одной PostgreSQL-транзакции.

Это команды будущего согласованного запуска; в production они не выполнялись. У оператора должен быть административный доступ к защищённой CLI/БД. CLI не публичный HTTP endpoint, не устанавливает scheduler и не включает себе feature flag. Поток можно планировать только после отдельного разрешения и организации независимых свежих manifests. Наличие команды не доказывает наличие production cron.

Политика producer изначально disabled/unapproved, settings `{}`. Обязательные явно утверждаемые настройки: `min_confidence`, `max_manifest_age_seconds`, `issuer`, `required_sources` (непустой точный набор обязательных upstream sources). Никаких бизнес-значений по умолчанию. Одного флага недостаточно: нужны также approved DB policy, свежий source, подпись, идентичность и consumer policy/budget.

Перед выпуском проверяются: текущий SUCCEEDED опубликованный run; последний закрытый день Europe/Moscow; неизменный source_revision; отсутствие queued/processing imports; актуальная INACTIVITY policy и scope Авиаторов / Оптовик+Корпоративный `/2`; достаточный confidence; только ACTIVE/eligible CHURN_RISK; verified identity/manager без конфликтов. Изменение формул CHURN_RISK не выполнялось.

Локальная транзакция использует общий SOURCE_LOCK и блокировку policy/identity; поток sequence выделяется под policy lock. Два конкурентных release одного source run дают RELEASED/EXISTING и одну публикацию. Другой proof или policy revision не перезаписывает старую публикацию — IMMUTABLE_RELEASE_CONFLICT. Preview также сообщает этот конфликт.

Новые endpoints под прежней Bearer-авторизацией:

- `GET /api/integrations/calltrack/analytics/automation/publications`
- `GET /api/integrations/calltrack/analytics/automation/runs/{release_id}/changes`

Схема страниц/cursor совместима с потребителем; отдельные release ID/sequence, отдельные стабильные approved signal UUID (UUID5 от исходного эпизода), внешний subject.kind=external_id, key=ClientsVR client_id, expected manager_id. Это предотвращает конфликт payload/revision с ранее принятым SHADOW receipt. Provenance связывает approved event с исходным shadow signal/run и proof hash.

Выдача прекращается при producer disabled/unapproved; policy revision mismatch, pending import/source revision change, истечении manifest, смене закрытого дня, отзыве/revision смене binding дают 410. Старые строки остаются неизменными. Сохранённый consumer checkpoint при 410 не сбрасывается автоматически: нужен review остановленной доставки и согласованное восстановление. Нельзя пропустить cursor вручную, выдавая это за доставку.

### 2. Почему STORED_SALES остаётся UNKNOWN

Stored sales, completed import, MAX(sale_date), import filename и локальный checksum не доказывают, что 1С/FTP передали все необходимые данные. Поэтому исходные run/signals/coverage/freshness остаются UNKNOWN/SHADOW/false даже после отдельного разрешённого release.

Реализован verifier независимого подписанного manifest. В production такого поставщика доказательств пока нет; встроенного «подписать текущую sales как полную» инструмента **нет**.

Нужно предоставить:

1. Утверждённый реестр обязательных источников для отдела/видов покупателя и владельца, отвечающего за полноту.
2. Независимую выгрузку/manifest 1С или upstream, подтверждающие полный период через последний закрытый день, точные source revisions, отсутствие пропусков и незавершённых исправлений.
3. Независимую сверку scoped клиент-день фактов: checks, positive_checks, net stored header revenue и число групп, с точным алгоритмом нормализации v1 и денежной сериализации.
4. Актуальную ClientsVR revision и независимо проверенные назначения/всю историю стабильного client_id, включая переименования и алиасы.
5. Выделенный доверенный issuer/секрет в защищённом окружении и утверждённый срок действия; процесс отзыва при новых данных/ошибках.

Формат envelope: `payload` + HMAC-SHA256 hex `signature`. Подпись считается над каноническим JSON payload: sort_keys, UTF-8/ensure_ascii=false, separators `,`/`:`, без пробелов. Выделенный shared key ≥32 символов; не хранить в Git, manifest или публичных командах. Ключ позволяет подписывать: права на него должны быть отделены организационно от обычного запуска аналитики. Подпись подтверждает issuer, а не истинность его заявления. Нельзя выдавать автоматически подписанный локальный snapshot за независимое upstream доказательство.

Обязательные поля payload:

- `version: 1`, approved `issuer`, `source_run_id`, integer `source_revision`, `as_of`, `complete_through` равный закрытому дню;
- `source_revisions`: точный словарь всех approved required_sources → непустая известная revision;
- `scope_hash` полного scope (включая client_keys), `fact_count`, `facts_hash`;
- `identities_hash` актуальных reviewed связей, `clients_current: true`, известная `clients_revision`, `complete: true`;
- aware ISO `issued_at`/`valid_until`, действующие сейчас и укладывающиеся в approved max age.

UNKNOWN/UNSET/NULL/NONE для обязательных текстовых подтверждений не принимаются. Неполный, просроченный, неправильно подписанный или не совпадающий с фактами manifest блокирует весь release. Новая policy не превращает старые SHADOW публикации в approved.

`facts_hash`: SHA256 канонического JSON массива строк, отсортированных по client_key/sale_date: `{client_key, day, checks, positive_checks, revenue}`; day ISO date, revenue — точная десятичная строка PostgreSQL Numeric. В production verifier хэширует поток батчами, не загружает все факты в память. Ожидаемые факты должны поступить независимо из upstream; чтение аналитической таблицы для изготовления ожидаемого manifest запрещено процессом допуска. В тесте ожидаемые факты независимо построены из заранее известных синтетических входных продаж и сравнены с реальным snapshot.

`identities_hash`: канонический SHA256 списка `{subject_hash,client_id,manager_id,revision}` в порядке analytical client_key ACTIVE eligible CHURN candidates; subject_hash — digest точного normalized key. Scope/подписанные manifests содержат потенциально чувствительные идентификаторы: хранить приватно, не коммитить и не печатать.

### 3. Идентичность и назначение

Никакого автоматического fuzzy/name/phone assignment для approved events. Требуются reviewed producer bindings, устойчивый ClientsVR ID, однозначный Calltrack manager_id и независимое подтверждение актуальности ClientsVR. Evidence должно явно подтвердить `all_sales_reviewed`, `complete_client_history_reviewed`, `client_unique`, `manager_unique`, `nontechnical`, `no_name_phone_conflict`, reviewer и reason. REVIEW/AMBIGUOUS и отсутствующий manager не допускаются.

Если один client_id известен producer под несколькими VERIFIED аналитическими subject, он блокируется с `CLIENT_ID_MULTIPLE_SUBJECTS_REQUIRES_HISTORY_MERGE`. Нельзя рассчитывать «ушёл по старому имени», если клиент продолжает покупать под новым. Полное объединение его истории по stable ID — отдельная необходимая работа для таких клиентов; этот этап их безопасно исключает. Не отзывать старый alias только для обхода запрета, не доказав полноту истории.

Calltrack повторно проверяет собственный VERIFIED binding, соответствие external ID, фактического ответственного из ClientsVR, активного пользователя, expected manager из producer, срок proof, approved scope/source/client freshness и DB business policy. Общие имена/телефоны не используются как ключ задачи. В E2E две разные карточки имеют одинаковое отображаемое имя; задачи/бюджет не смешиваются по имени.

### 4. Lifetime pilot budget

`analytics_task_budget`: max_tasks NULL, approved=0, used_tasks=0. **Лимит 10 не установлен и не утверждён.**

Лимит общий для новых автоматических задач этой БД и постоянный, не сбрасывается по дням, run, рестарту или завершению задачи. Явное увеличение/отзыв через admin-only `api/analytics_budget.php`, POST с CSRF, revision/CAS и полями `{revision,approved,max_tasks}`; GET показывает budget. История конфигурации — analytics_task_budget_events. Изменять used_tasks API не позволяет; max_tasks нельзя уменьшить ниже уже использованного. Формального «начать новый пилот и обнулить» механизма нет.

Перед созданием проверяются budget и текущая policy. При фактической новой вставке counter резервируется под `FOR UPDATE` + условный UPDATE в той же транзакции, что task/slot/inbox. Отказ вставки откатывает counter. Дубликат signal/open slot возвращает existing task до резервирования и не расходует бюджет. Ручные задачи независимы от этого лимита автоматизации.

Отсутствие approval, исчерпание, отсутствие строки/таблицы или ошибка budget блокируют новую задачу; receipt сохраняется как BLOCKED с BUDGET_NOT_APPROVED / BUDGET_EXHAUSTED / BUDGET_UNAVAILABLE. После повышения cap эти receipts сами не превращаются в задачи: нужен согласованный повторный release/re-evaluation с повторной проверкой свежести. Существующие task/history не удаляются.

`CALLTRACK_ANALYTICS_FEED` default `shadow`; явное `approved_churn` выбирает только новую ленту. Отдельная `analytics_automation_sync_state` предотвращает перенос shadow cursor в approved stream. `CALLTRACK_ANALYTICS_MODE` default disabled; feed сам по себе не включает обработку. CLI MySQL lock остаётся общим для consumers одной БД.

## Проверки и результат

| Проверка | Результат | Доказательство |
|---|---|---|
| Реальные синтетические продажи → worker → CHURN_RISK | PASS | test_churn_automation_e2e.py, PostgreSQL |
| Штатный producer/signature/policy/identity gates → persisted release | PASS | Тот же тест, без подмены approved event |
| HTTP/Bearer → PHP → MariaDB task | PASS | churn_automation_e2e.php, настоящий transport |
| Ровно одна задача правильному manager, повтор | PASS | Три отдельных PHP процесса, same/different signals, hard cap 1 |
| DONE/result/history, повтор после завершения | PASS | Реальные MariaDB строки и task service |
| Budget approval/revoke/rollback/no reset | PASS | analytics_budget_test.php + real E2E |
| Другие типы, SHADOW, missing budget | PASS | Hard phase1 gate, настоящий SHADOW export |
| Сетевой сбой и частичная запись | PASS | Реальный HTTP с инъекцией временного отказа; MariaDB trigger на второе receipt, checkpoint сохранён, retry без потери/дублей |
| Просрочка/подпись/UNKNOWN/неполная policy/identity/conflict/alias | PASS | Отрицательные ветви реального PostgreSQL producer |
| Отзыв policy/identity, pending import | PASS | HTTP 410 после изменения реальных fixture rows |
| Локальные миграции | PASS | PostgreSQL upgrade/downgrade в disposable schema; MariaDB migration SQL применяется без преобразований |
| Реальная upstream полнота/актуальность/bindings | BLOCKED | Источник подтверждений и согласования владельца не предоставлены |
| Production transport, runtime schema/flags/scheduler/UI | NOT VERIFIED | SSH доступа нет, production не запрашивался |

Расширенная целевая Python-регрессия: **276 passed, 0 skipped** (включены прежние PostgreSQL inactivity/delivery/RFM/buyers/category tests). Два предупреждения deprecation зависимости websockets тестового uvicorn, не ошибка бизнес-сценария. Финальный повтор после изменений подтвердил 276 passed, без пропусков.

PHP SQLite (network none): 7 scripts PASS — manager_bindings, analytics_tasks, analytics_policy, analytics_sync, analytics_sync_contract, analytics_producer_contract, analytics_budget. Синтетический consumer-only тест предыдущего аудита остаётся вспомогательным и не выдается за E2E. Главный новый E2E использует настоящий producer и MariaDB.

Новые тесты Sales: `test_churn_automation_e2e.py`, `test_churn_automation_migration.py`, `test_churn_automation_management.py`. Calltrack: `churn_automation_e2e.php`, `analytics_budget_test.php`; существующие fixtures обновлены для строгого phase1 контракта. Прежние незакоммиченные тесты/исправление malformed cursor из аудита сохранены в этих ветках.

## Состав новых файлов и миграций

Sales Journal:
- config.py: новые закрытые по умолчанию настройки;
- models/analytics_automation.py + models/__init__.py: пять новых таблиц;
- services/analytics_automation.py: release/verifier, versioned policy/identity management;
- analytics_automation.py: operator CLI;
- routers/analytics_delivery.py: отдельные read endpoints;
- Alembic `20261022_24_churn_automation.py`, после `20261020_23`;
- три новых phase1 tests и этот runbook.

Новые таблицы: analytics_automation_policy, analytics_automation_identities, analytics_automation_releases, analytics_automation_events, analytics_automation_decisions. Исходные sales/clients/SHADOW схемы и записи не переписываются.

Calltrack:
- api/analytics_budget_service.php, api/analytics_budget.php;
- api/manager_tasks_service.php: transactional reservation только новой automatic task;
- api/analytics_integration.php: CHURN-only, строгий native approval contract, stable ID/expected manager/proof expiry и budget gates;
- api/analytics_sync.php: отдельный feed/checkpoint плюс сохранённое исправление malformed pages;
- `database/20261022_analytics_budget.sql`: budget, audit, approved sync cursor;
- тесты/fixtures и `docs/churn-risk-phase1.md`.

## Воспроизведение стенда

Это инструкция **только для локального синтетического стенда**. Не использовать production DSN, проект/контейнер/volume или token.

1. Локальный Docker daemon, существующие образы postgres:16-alpine, mariadb:11.4 и analytics-php-test:local. Создать отдельную internal network `churn-phase1-test`; контейнеры `churn-phase1-pg`, `churn-phase1-mariadb`, временные data dirs, MariaDB alias analytics-test-mariadb. Не монтировать production volumes. Кластер должен быть disposable.
2. PostgreSQL local port 55439, только localhost; БД analytics_shadow_test, rfm_test, calltrack_buyers_test. В текущем окружении internal Docker network не публиковала порт; использован localhost TCP forward к IP **своего** тестового PG, без внешних сетей. Это инфраструктура теста, не конфигурация приложения.
3. Установленные зависимости не менялись. Приватный тестовый DSN передать в ANALYTICS_SHADOW_TEST_DATABASE_URL / RFM_TEST_DATABASE_URL / TEST_BUYERS_DATABASE_URL, CHURN_PHASE1_E2E=1, CALLTRACK_ROOT на отдельный checkout. Пароли только синтетические, не реальные.
4. Запустить новые три теста и целевой прежний набор. E2E создаёт отдельную UUID PostgreSQL schema, fixture HTTP на 18769, synthetic ClientsVR endpoint, выполняет worker, release и конкурентный release, затем PHP test container в internal network. Guarded MariaDB analytics_sync_test сбрасывается — это исключительно ephemeral БД теста. Имена/host guards обязательны.
5. Python fixture удаляет только собственную UUID schema. PHP test container read-only, /tmp tmpfs, удаляется после запуска. После проверки остановить только созданный тестовый стенд. Production команд/cron в тесте нет.

## Неутверждённые параметры / информация

- min_confidence producer и consumer, приоритет и due days CHURN_RISK;
- production max_tasks (10 — лишь предложение владельца, не настройка);
- issuer, required_sources и схема независимых upstream revisions/completeness;
- max manifest age, source/client attestation lifetime, max signal age;
- проверенные исторические bindings и expected Calltrack manager IDs;
- protected signer key provisioning, владелец подписания и отзывов;
- частота worker/manifest ingestion/release/consumer, monitoring SLA и порядок ручного release заблокированных receipts.

Тестовые .5, cap1, HIGH/1 day и сроки proof применялись только к synthetic DB fixtures и не являются рекомендацией/утверждением production-политики.

## Безопасный перенос и первый запуск — не выполнено

1. Review отдельных веток и зависимостей, затем отдельное разрешение на commit/push/PR/merge/deploy по необходимости. Учитывать локальные pending изменения предыдущего аудита. Calltrack production/max-message-sync расходится с upstream — не делать blind pull.
2. Сверить server HEAD/status/файлы/образ/schema/flags/cron read-only. При расхождении остановиться; не перезаписывать server-only функции. Подтвердить production source/contracts и политику владельцем.
3. До deploy остановить согласованные analytics/release/consumer расписания, режим consumer disabled, producer automation false, policy unapproved. Backup code/image и БД, сохранить существующие backup/untracked/private data. Применение обеих additive миграций — только после отдельного разрешения. Backend CMD автоматически запускает Alembic: сборка/запуск нового образа не считается переносом «без миграций».
4. Deploy disabled; проверить health, schema, старые read APIs и SHADOW, disabled consumer network_requests=0. Не включать signal worker, release или consumer автоматически.
5. Настроить проверенный независимый upstream manifest provider и source registry, private key management, reviewed identities/full histories и managers. Проверить source revision change, stale proof, revoke и 410 на согласованном staging. Не подписывать UNKNOWN как полное локальной утилитой.
6. Внести только утверждённые draft параметры, отдельно утвердить producer/consumer policy. Бюджет оставить unapproved до явного разрешения на pilot и числа задач. Выполнить согласованный preview и dry_run, сверить весь список/причины. Producer preview read-only; consumer dry_run пишет inbox/audit/cursor/cache, но не задачи. Согласовать эти записи отдельно.
7. По отдельному разрешению на реальные задачи: зафиксировать pilot budget, защитить доступ к admin budget API; настроить approved_churn feed и ограниченный scheduler. Первая партия вручную сверяется, used_tasks/cursors/inbox/tasks сверяются между системами. Изменения источника/manager/identity отзывают допуск, не создают silent reassignment.
8. Мониторинг: release decisions/refusals, source proof expiry/revisions, HTTP failures/410, current_run/page cursor, rejections, BUDGET_* counts, used/max_tasks, новые/существующие задачи, менеджеры, completion/history и latency. Любая неоднозначность — stop/review, не очистка history.

## Аварийная остановка и откат

Сначала disabled consumer + остановка scheduler, отзыв DB task policy и budget approval; выключить/отозвать producer policy и остановить release jobs/worker по разрешению. Блокировки policy/budget координируют отзыв с транзакцией, но уже committed task не отменяется сменой флага. Пересчитать последние фактические задачи и budget.

Не удалять inbox/cursors/releases/tasks/bindings/history и не обнулять used_tasks. Откат code/image к сохранённому reviewed SHA с additive tables retained. Автоматический destructive downgrade запрещён. Ошибочные задачи исправлять адресно с историей после проверки; не закрывать/переassign массово. Для повторного запуска заново проверить источник, expiry, policy revisions, identities и budget; старые непроверенные публикации не повышать до approved.

**Статус:** native phase1 технически продемонстрирован на настоящем изолированном стенде. Реальный источник и production разрешения остаются закрытыми до отдельного решения владельца.
