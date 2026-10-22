# Готовность цепочки Analytics Signals → задачи Calltrack

Дата: 22.10.2026. Проверки выполнены в изолированном облачном окружении Codex. Production не запрашивался и не изменялся.

Этот документ — исторический аудит до реализации phase 1. Актуальное состояние штатного producer и настоящего E2E см. в [churn-risk-phase1.md](../churn-risk-phase1.md).

## Решение

**Автоматизацию production запускать нельзя.** Текущий продукт — работоспособный в проверенных пределах диагностический INACTIVITY SHADOW и отдельно реализованный потребитель задач с защитными проверками. Штатного пути выпуска разрешённых автоматических сигналов пока нет.

Главный критерий «штатный анализ → штатная публикация → разрешённый сигнал → ровно одна задача» **BLOCKED**: producer допускает только SHADOW, всегда возвращает `automation_allowed=false`; STORED_SALES намеренно сохраняет `coverage_status=UNKNOWN`, `business_freshness=UNKNOWN`. Ослаблять эти ограничения для прохождения теста нельзя.

Проверен другой, честно разделённый результат:

1. Синтетические продажи → штатные функции статистики, reconcile и экспорт → настоящий SHADOW CHURN_RISK → обработчик Calltrack → BLOCKED, ноль задач.
2. Независимый **consumer-only** синтетический AUTOMATION_APPROVED-конверт, с явно вымышленным подтверждением полноты и утверждённой тестовой политикой → реальный Calltrack + SQLite → одна задача менеджеру №3 → повтор → по-прежнему одна задача → DONE и сохранённая история.

Второй пункт не является разрешением producer, production-политикой или полным E2E. SQL чтения исходных синтетических продаж выполнен на SQLite; PostgreSQL-хранилище signal/run/change заменено адаптером захвата ORM-записей. Worker scan, PostgreSQL-транзакции и атомарная публикация в этой цепочке не выполнялись.

## Окружение и версии

- Sales Journal `/workspace/sales-journal-integration`, ветка `audit/signal-task-readiness`, основана на `0dc54a0a4852b558742c3db34129241170b0ca07`. Доступный GitHub main после fetch — `de0a0c88fdc2ba3bea1558e7fa313139a16e96f3`; предыдущее исправление входит в main. Новые изменения текущего аудита локальные.
- Calltrack `/workspace/calltrack-verification`, ветка `audit/signal-task-readiness`, основана на `91cd70649e7ad722390f98107db5d6fa50735a84` (тестовое исправление поверх `530adc1`). Новые изменения текущего аудита локальные.
- Production-коммиты de0a0c8/530adc1, healthy и сверка реального клиента — сведения владельца, здесь повторно не подтверждались.
- PHP CLI на хосте отсутствует, но найден уже существующий локальный `analytics-php-test:local`. Тесты выполнены через локальный Docker socket, с `--network none`, read-only rootfs/checkout, временным `/tmp`. SQLite находится в памяти, тестовые файлы кэша — в tmpfs.
- PostgreSQL/MariaDB экземпляры и БД не создавались. Учитывая ранее установленное ограничение на новую тестовую PostgreSQL, эти проверки оставлены NOT VERIFIED. Production БД для тестов не использовалась.

## Карта реализации

### Источник, RFM и INACTIVITY

Продажи поступают через importer/autoload (FTP и XLS/XLSX/HTML) в `sales`/`sale_items`; import_batches хранит статусы и периоды, source_revision инвалидирует аналитический источник. Наличие максимальной даты или completed импорта не доказывает полноту upstream.

`rfm_data.rfm_rows()` считает по lower(trim(name)): recency от последней покупки, frequency по чекам, monetary по сумме чеков, средний чек, интервалы по уникальным дням. `rfm.enrich()` использует tied quintiles, не разрывает одинаковые значения; сегменты определяются явным приоритетом риска/новизны/ценности. RFM rebuild создаёт неизменяемую генерацию, проверяет число строк и переключает active pointer в одной транзакции; имеются advisory locks. Перестроение вызывается после завершённого импорта и изменения продаж. Ошибка обновления сохраняет прежний snapshot. **Изменение RFM-сегмента само по себе не создаёт событие Analytics Signals.**

`analytics_scope.py`: отдел Авиаторов, ClientsVR Оптовик/Корпоративный; аналитическая нормализация v1; ровно один из двух целевых видов включается, оба исключаются. Policy `/2`. Нет отбора по `sales.price_type`, нет автоматического приравнивания его к buyer_type. Менеджер при расчёте INACTIVITY не назначается.

`analytics_source.py`: последний закрытый день в бизнес-таймзоне; pending imports, отсутствие scoped sales, ошибка ClientsVR блокируют расчёт. Stored rows достаточны для диагностики, но не подтверждения полноты/свежести. Валидный JSON списка ClientsVR не доказывает отсутствие незаметного усечения, общего snapshot двух ответов и актуальности кэша.

`inactivity.py`: минимум 8 покупочных дней, 7 интервалов, span 56 дней, MAD/median ≤0.75, median ≤60; окно цикла 365, reference revenue 180 дней. Overdue = max(ceil(1.5 median), ceil(Q75)+2, ceil(median)+2); churn = max(ceil(3 median), ceil(Q75)+7, 14). Рассчитываются score/confidence; UNKNOWN coverage не получает вес подтверждения. Формулы не менялись. Снижение объёма выручки без просрочки не является отдельным сигналом.

`analytics_signal_store.py`: CREATED, UPDATED, ESCALATED, DEESCALATED, SUSPENDED, RESUMED, RESOLVED; эпизод привязан к последней покупке. Новая покупка разрешает старый эпизод; оценка сохраняется отдельно. Недостаточная история/нерегулярность не дают активный обоснованный сигнал. Актуальность версии scope/source/algorithm проверяется при resume. Изменение несовместимых параметров даёт SUPERSEDED, прежний опубликованный run сохраняется.

`analytics_signal_worker.py` и CLI: scan/evaluate батчи; PostgreSQL only, advisory source/leadership locks, state row lock, source revision/fingerprint и замороженная дата. RUNNING/PAUSED/FAILED могут возобновляться по checkpoint, ошибки регистрируются с проверкой актуального heartbeat. В Compose имеется opt-in profile `analytics-shadow`, `restart: no`; CLI worker опрашивает по умолчанию раз в 60 секунд после завершения/блокировки. Это наличие механизма, **не подтверждение работающего scheduler в production**. Для расчёта нужны одновременно env enabled и durable control enabled.

### Публикация и доставка

`analytics_delivery.publish_delivery()` присваивает sequence под блокировкой delivery_head и уникальным run_id, в транзакции с завершением run/публичным pointer. HTTP ничего не отправляет наружу. `latest` — состояние, `publications` — курсор sequence, `runs/{id}/changes` — курсор audit ID внутри неизменяемого SUCCEEDED run. При отсутствии run корректны null/empty/404. Export требует Bearer и всегда SHADOW/false, убирает телефоны и scope client_keys.

Calltrack инициирует pull: `api/analytics_sync.php`, CLI-only, защищённый integration token в `sales_journal.php`, HTTP/JSON ошибки не превращаются в успешную доставку. CLI использует MySQL connection lock. `scripts/sync_analytics_cron.sh` — обёртка с default disabled, **не устанавливает cron**. Частота production не подтверждена.

`analytics_sync_state` хранит publication_sequence/current_run_id/current_sequence/after_change. Каждый receipt/решение/задача фиксируется транзакционно; page cursor — после всей страницы. Сбой после первого события до page checkpoint приводит к replay, а не пропуску. Невалидные события и конфликтующие ревизии карантинируются в `analytics_delivery_rejections`; это не успешное бизнес-исполнение, очередь отклонений требует контроля/ручного восстановления. Не удалять и не сбрасывать cursors для повтора.

Одинаковый signal/revision с другим payload вызывает конфликт; старая revision игнорируется. Новая revision того же эпизода не создаёт новую задачу. Payload hash, inbox и processing history сохраняются. Отдельная атомарность всех HTTP/БД шагов отсутствует; устойчивость основана на checkpoint + идемпотентном replay.

### Идентификация, задачи и результат

Для автоматических задач **нет fuzzy/phone/name matching**: необходим analytics_client_bindings VERIFIED для subject (kind/version/key + точный scope), nontechnical и существующий ClientsVR client_id. Дубликаты/неоднозначность/нет подтверждения блокируют. Это не доказывает, что ранее вручную подтверждённая группа по имени действительно содержит одного клиента: такую группу нужно проверять полностью.

ClientsVR даёт текущего ответственного, `client_manager_bindings` связывает обозначение с активным пользователем Calltrack. Отсутствие mapping, конфликт менеджеров или неактивный пользователь блокируют. Не переносить автоматически существующую задачу при смене менеджера/клиентской привязки.

`processAnalyticsEvent()` → `analyticsGate()` → `createManagerTask()`. Только INACTIVITY PURCHASE_OVERDUE/CHURN_RISK. Текст включает причину, последнюю покупку, интервал и дни отсутствия; задача содержит client_id, assignee, priority, UTC due_at, исходный signal/evidence и версию bindings. История — manager_task_events.

UNIQUE source_signal_key предотвращает повтор той же задачи; analytics_open_task_slots ограничивает активную задачу на client_id/family даже между эпизодами. После закрытия старой задачи новый обоснованный эпизод может создать новую; тот же эпизод после DONE не открывается снова. Изменение/разрешение сигнала ставит задачу на review и не закрывает выполненную работу автоматически.

UI: вкладка «Задачи», manager-tasks.js, manager_tasks.php. OPEN → IN_PROGRESS → DONE; CANCELLED только администратором; результат обязателен для terminal статуса. Менеджер видит/обновляет свои задачи, supervisor читает, admin назначает. Оптимистическая version защищает от stale updates. HTTP/session/CSRF UI в текущем аудите не запускались, ACL/lifecycle сервисов проверены PHP-тестами.

## Обнаруженный и исправленный дефект доставки

**Высокий риск пропуска/скрытой остановки:** отсутствующее/неправильное поле items публикаций считалось пустой лентой; отсутствующее next_after страницы изменений считалось концом run. Проверка невалидного cursor производилась после записи receipts.

Теперь до обработки проверяются список items, размер, наличие next_after, bool automation_allowed, допустимость и прогресс cursor; явный корректный пустой ответ сохраняет прежнее поведение. malformed ответ вызывает ошибку и не переводит checkpoint за необработанные данные. Обновлена fixture старого теста до реального контракта API.

Регрессия `analytics_sync_contract_test.php` **падала до исправления** с `Malformed page silently accepted`, после исправления проходит. Отдельно проверена ошибка cursor при наличии события: нет даже записи rejection до проверки страницы.

## Результаты по этапам

PASS относится только к указанному объёму проверок. NOT VERIFIED не равен FAIL.

| Этап | Реализован | Проверен тестами | Готов к production-автоматизации |
|---|---|---|---|
| Анализ продаж | Да, INACTIVITY SHADOW/RFM | PASS функций; PostgreSQL NOT VERIFIED | Нет |
| Формирование сигналов | Да, два INACTIVITY типа | PASS синтетического расчёта/reconcile | Нет |
| Публикация сигналов | Да, SHADOW | PASS контракта; PostgreSQL atomicity NOT VERIFIED | Нет |
| Получение Calltrack | Да | PASS replay/error/partial page SQLite + mock transport | Нет |
| Определение менеджера | Да, verified bindings | PASS PHP/SQLite | Нет, реальные mappings не сверены |
| Создание заданий | Да, gated consumer | PASS consumer-only approved fixture | Нет, штатный разрешённый producer отсутствует |
| Защита от дублей | Да | PASS sequential replay/open slot/DONE; MySQL race NOT VERIFIED | Нет |
| Контроль выполнения | Да | PASS service ACL/version/status/history; UI NOT VERIFIED | Нет |
| SHADOW | Да | PASS, настоящий producer export не создаёт задачу | Да для проверенных защит; запуск worker требует разрешения |
| Безопасная активация | Частично | PASS gate/revocation/default disabled; полный путь BLOCKED | Нет |

## Все 15 требуемых сценариев

| № | Результат | Доказательство/ограничение |
|---|---|---|
| 1 Объём покупок снизился | NOT SUPPORTED как отдельный trigger | Новый Python-тест: падение reference revenue при прежнем цикле не создаёт сигнал; правило не выдумано |
| 2 Клиент перестал покупать | PASS функций | 16 еженедельных синтетических покупок, 30 дней паузы → CHURN_RISK |
| 3 Изменился RFM-сегмент | NOT SUPPORTED как trigger | RFM snapshot независим, segment не вход INACTIVITY |
| 4 Сигнал передан | PASS контракта | Сериализован штатным export, прочитан PHP, распознан; HTTP transport mock, production нет |
| 5 Известный менеджер | PASS consumer-only | Approved fixture → stable client_id, manager 3 |
| 6 Нет менеджера | PASS | analytics_tasks_test missing/unmapped/inactive blocks |
| 7 Одинаковые имена | PASS consumer gate | Разные verified IDs не объединяются; правильность ручной групповой привязки NOT VERIFIED |
| 8 Общий телефон | PASS consumer gate | Без binding заблокировано, имя/телефон в автоматическом gate не участвуют |
| 9 Повтор | PASS sequential | Ровно одна задача и неизменный task_id |
| 10 Сетевой сбой | PASS mock transport | frozen cursor сохранён, retry обрабатывает событие |
| 11 Частичный пакет | PASS SQLite | Trigger отказа второго INSERT; первое receipt остаётся, cursor не продвинут; retry завершает без дубля |
| 12 SHADOW | PASS | Настоящий экспорт + даже enabled consumer/approved test policy → BLOCKED, 0 задач |
| 13 Тестовая разрешённая автоматизация | PASS consumer-only; E2E BLOCKED | Независимый synthetic approved конверт создаёт задачу; producer такой конверт не выпускает |
| 14 Выполнение | PASS service | DONE + result/history, другой менеджер запрещён, stale version конфликтует |
| 15 Повторный расчёт | PASS mock reconciliation/replay; PostgreSQL NOT VERIFIED | Эпизоды и old revision/task slot протестированы, полноценный worker в БД не запускался |

## Фактически выполненные тесты

Python: целевой набор предыдущей интеграции + `test_signal_task_contract_export.py`, `test_rfm.py`, `test_rfm_snapshot.py`: **249 passed, 24 skipped**. Пропущены: buyers PostgreSQL 1, categories PostgreSQL 1, inactivity/delivery PostgreSQL 15, RFM snapshot PostgreSQL 7.

PHP в локальном Docker, SQLite in-memory, сеть отключена: **6 скриптов PASS**:

- manager_bindings_test.php
- analytics_tasks_test.php
- analytics_policy_test.php
- analytics_sync_test.php (с новым синтетическим export; не PostgreSQL fixture)
- analytics_sync_contract_test.php
- analytics_producer_contract_test.php

`php -l api/analytics_sync.php` успешен. CLI по умолчанию: `{"mode":"disabled","network_requests":0}`.

Новая связка воспроизводится так: `SYNTHETIC_SIGNAL_EXPORT=/workspace/scratch/synthetic-shadow-event.json PYTHONPATH=backend .../pytest backend/tests/test_signal_task_contract_export.py`, затем PHP-скрипты с `ANALYTICS_ENGINE_EVENT_JSON` на этот fixture. Он содержит только вымышленные данные, не коммитится. SQLite schema fixture применяет диалектные преобразования SQL миграций исключительно к in-memory базе; production-миграции не запускались.

PHP запуск: локальный unix Docker socket, образ `analytics-php-test:local`, `--rm --network none --read-only --tmpfs /tmp`, checkout mounted read-only в /code, synthetic fixture read-only в /fixtures/event.json, `CALLTRACK_STORAGE_DIR=/tmp/calltrack`. Никакие существующие контейнеры не перезапускались.

PostgreSQL isolation/locks/atomic publication/resume и MySQL connection lock/FOR UPDATE/concurrent writers/restart durability не подтверждены SQLite. Реальный HTTP/TLS/Bearer между системами и authenticated UI текущим набором не проверены.

## Изменения текущего аудита

Sales Journal:
- `backend/tests/test_signal_task_contract_export.py` — synthetic sales, штатные статистика/reconcile/export, запрет автоматизации, scope redaction, поддержанные/неподдержанные triggers.
- `docs/audits/analytics-automation-readiness-20261022.md` — этот отчёт.

Calltrack:
- `api/analytics_sync.php` — строгая проверка страниц и cursor до эффектов.
- `tests/analytics_sync_test.php` — актуальный envelope и честное описание происхождения fixture.
- `tests/analytics_sync_contract_test.php` — malformed/empty/cursor regression.
- `tests/analytics_producer_contract_test.php` — producer SHADOW block, независимый approved consumer fixture, assignment/replay/DONE/history и partial failure retry.

Новых коммитов/PR/push в этом аудите нет. Предыдущие опубликованные коммиты 0dc54a0/91cd706 не содержат новых исправлений этого отчёта.

## Что блокирует реальный запуск и что ещё разработать

1. **Producer authorization отсутствует:** Literal SHADOW в settings, SHADOW в БД/сигналах/экспорте, false в источнике/формулах. `ANALYTICS_SHADOW_MODE` не является действующим переключателем; фактический параметр — `ANALYTICS_SIGNALS_MODE`. Флаг enabled включает расчёт, не право создавать задачи. Нужен отдельный согласованный путь безопасного выпуска разрешённых событий; не менять старые immutable SHADOW-публикации.
2. **Доказанная полнота/свежесть источника отсутствует:** необходимо согласовать необходимые upstream sources, complete_through/source_revision, проверки пропусков/исправлений, срок действия и отзыв подтверждения. Нельзя подставлять MAX(sale_date), completed или утверждение из файла без проверяемого процесса. Нужна также актуальная ClientsVR revision/cache attestation.
3. **Политика бизнеса не утверждена:** production confidence, priority, due days, per-type enable и approval остаются не заданными/неутверждёнными. Примерные 0.65/NORMAL3/HIGH1 не получили нового разрешения. Test fixture 0.5/HIGH1/NORMAL3 — только тест.
4. **Проверенные bindings:** аналитический нормализованный ключ может объединять одноимённых реальных клиентов до привязки. Не подтверждать группу без проверки всех её продаж. Неоднозначные/технические группы — REVIEW/AMBIGUOUS, никаких задач. Менеджер только explicit active binding.
5. **Нет жёсткого budget для первого запуска:** maxPages ограничивает страницы (обычно 5 × 50), а не общее количество задач за день/между запусками. Нужны согласованные allowlist клиента/менеджера, hard cap новых задач на run/day, атомарное резервирование бюджета и стоп при достижении, approved dry-run manifest. Значения не придуманы и механизм не включён.
6. **Runtime/схема/планировщик/мониторинг не сверены:** production SSH нет; версия исходников не доказывает версию образа/cron/schema. Calltrack ветки расходятся, слепой pull запрещён.
7. **Обязательный штатный E2E не выполнен:** PostgreSQL и MySQL staging + реальный transport необходимы до признания автоматизации готовой. Consumer-only fixture — не замена.

### Дополнительный риск replay

Receipt и checkpoint не являются exactly-once доставкой; это at-least-once чтение и идемпотентные эффекты. Блокированные receipts сохраняются, но после продвижения cursor сами не обязательно вернутся в обработку при изменении политики. Есть per-signal admin dry-run. Перед разрешённым запуском нужен согласованный re-evaluation/release flow, который перепроверяет актуальность, а не переиспользует устаревший approved payload. Нельзя очищать inbox или менять source_signal_key для обхода дедупликации.

## Производительность buyers

29.665 секунды `/buyers`, 1.32 млн sales и 9414.707 ms phone grouping — предоставленные измерения, здесь не повторялись. Sync/tasks не вызывают `/buyers`; это не прямой обязательный шаг автоматического создания. Но полный scan/сортировка телефона конкурирует за CPU/I/O PostgreSQL с worker/import/RFM; source locks и 30-секундный timeout могут сделать диагностику недоступной. Это отдельная задача оптимизации: безопасный EXPLAIN по согласованию, нормализованные/индексированные ключи или предагрегация phone ownership, точный plan/ANALYZE на staging. Не добавлять production index/DDL в рамках этого аудита. Проверить также быстрый indexed SQLite directory lookup vs fallback full cache scan в Calltrack.

## План контролируемого внедрения — не выполнен

1. Сначала согласовать разработку отсутствующего approved producer, source evidence и budget, бизнес-параметры. До этого внедрять можно только отдельно проверенное исправление consumer contract с disabled режимом.
2. Read-only сверка actual server HEAD/status, образа, source hashes, Alembic, Calltrack tables/constraints, flags, cron/systemd; не печатать secrets/client data. При расхождении серверных файлов остановить перенос и сверить diff вручную.
3. Получить разрешение на staging PostgreSQL/MariaDB и настоящий cross-process E2E. Проверить конкурентные consumers/импорты, аварийный выход после task commit до cursor, publication rollback, revoke во время транзакции, неполный каталог, invalid pages, terminal tasks, replay старых публикаций.
4. Отдельное разрешение на deploy/миграции. Backup code/image + согласованный DB snapshot; backup-папки и посторонние untracked сохраняются. Собирать из чистого reviewed контекста, исключив backup/data/secrets. Backend CMD автоматически делает Alembic — обычный restart нового образа не считать «без миграций».
5. Сначала развернуть disabled/SHADOW, без scheduler/consumer enabled; проверить health/schema/flags и zero network у disabled consumer. Новые immutable publications не означают право автоматизации.
6. Только после отдельного разрешения диагностический SHADOW/dry_run: учесть записи в analytics/inbox/checkpoint/cache, нагрузку и блокировки. Просмотреть весь кандидатный список и blockers приватно. Утвердить identities, managers, source evidence, политику и pilot manifest.
7. После успешного штатного staging E2E и отдельного разрешения владельца — ограниченный pilot с hard cap/allowlist, мониторингом new tasks/replays/rejections/latency и ручным подтверждением первой партии. Без реализованного budget этот этап запрещён.

## Остановка и откат

Немедленно остановить/revoke при wrong identity/manager, дубле, превышении бюджета, stale/missing source evidence, source revision mismatch, malformed feed, растущем backlog/rejections, ошибках locks/timeout или неожиданном режиме. Сначала остановить consumer scheduler и выставить disabled, отозвать DB policy approval; затем остановить producer worker. Сам факт изменения флага не отменяет уже завершённую транзакцию: сверить последние tasks/checkpoint.

Сохранить inbox, publication log, bindings, tasks и историю. Откатывать code/image к сохранённому reviewed SHA с additive schema retained, не делать destructive downgrade автоматически. Не удалять/не закрывать задачи менеджеров массово; ошибочные назначения исправляются после отдельной проверки с аудитом. После отката health/disabled flags/read-only counters, сверка созданных задач и повторное согласование. Cursors не обнулять.

**Условия допуска:** подтверждённый источник/каталог и непросроченные revisions; безопасный approved producer; verified client/manager; согласованная DB business policy; свежие immutable события; жёсткий бюджет/allowlist; настоящий staging E2E включая concurrency/recovery; проверенная production схема/образ/scheduler/monitoring/kill switch; отдельное явное разрешение владельца. Сейчас эти условия совместно не выполнены.
