# CHURN_RISK phase 1: подготовка внедрения, 22.10.2026

**Это план, не разрешение и не выполненное внедрение.** Production не запрашивался. Все команды с изменением сервера/БД выполняются владельцем только после отдельного согласования. Развёртывание disabled и разрешение реальных задач — два разных решения.

## Git-базы и серверные зависимости

| Проект | База PR | Связь с последней сообщённой production-версией |
|---|---|---|
| Sales Journal | main: de0a0c88fdc2ba3bea1558e7fa313139a16e96f3 | Это последний сообщённый production SHA. Содержит 0dc54a0; дерево исходной feature-базы совпадает с деревом main |
| Calltrack | feat/analytics-manager-bindings: e7d5e96f350f36c1ddb43f1a1be62227ff146d48 | Содержит 530adc154ff521e21148473bfae0834430ea9351 и 91cd706; после 530adc1 менялся только тест категорий |

Calltrack main ce4830a не содержит всего модуля задач. PR в main захватил бы прежнюю интеграцию; поэтому база — feature-интеграция. Удалённая production/max-message-sync 715be84 расходится с сообщённой серверной 530adc1. Не делать blind pull, reset, force push или автоматически объединять серверную ветку. Новые PR требуют отдельных reviewer решений, не выполняют merge/deploy.

Фактические server HEAD/status/image/schema неизвестны. Получить их read-only по инструкции в Calltrack `docs/churn-risk-production-readonly.md`. При другом HEAD, локальных изменениях, несовпадении базовых файлов или незнакомой схеме остановиться и сверить server-only функции. Наличие GitHub ancestor не доказывает совпадение запущенного образа.

## Состав и закрытые defaults

Описание механизма, manifest и тестов: [churn-risk-phase1.md](churn-risk-phase1.md). Рабочая ветка обоих проектов `feat/churn-risk-automation-phase1`.

Producer flag false, signer key пуст, DB policy disabled/unapproved, исходный worker остаётся SHADOW. Calltrack mode disabled, feed shadow, business policy не утверждена, budget max_tasks NULL / approved=0 / used_tasks=0. Лимит 10 и рекомендуемые ранее 0.65/HIGH/1 день не активированы и не утверждены.

Исходные SHADOW run/signals сохраняют UNKNOWN/false. Approved release — отдельный поток с отдельными ID, independent evidence и cursor, не повышение старого payload изменением флага.

В подготовку входит небольшой связанный hardening: malformed feed page/cursor не принимаются за пустой успех; при появлении нового VERIFIED alias того же client_id после release его выдача прекращается с 410. Последнее повторно проверено настоящим PostgreSQL/HTTP E2E.

## Миграции и порядок

### Sales Journal / PostgreSQL

Ожидаемая исходная Alembic revision: **20261020_23**. Если она другая, этот план не применять автоматически.

Новая миграция: `backend/alembic/versions/20261022_24_churn_automation.py`, revision `20261022_24`, down_revision `20261020_23`.

Создаёт только:

1. analytics_automation_policy (одна начальная строка disabled/unapproved/settings `{}`/sequence0);
2. analytics_automation_identities;
3. analytics_automation_releases;
4. analytics_automation_events и ix_automation_release_event(release_id,id);
5. analytics_automation_decisions.

FK release.source_run_id → analytics_signal_runs, event.release_id → releases; UNIQUE source_run_id и sequence. sales/sale_items/старые SHADOW записи не переписываются. Риски: DDL/catalog locks, требования прав на CREATE/FK/sequence, несовпадение исходной revision/частично созданные объекты. Применение PostgreSQL upgrade/downgrade проверено только в disposable test schema.

### Calltrack / MySQL или MariaDB

Сначала сверить все прежние таблицы и UNIQUE/FK/индексы по SQL-файлам. Наличие кода 530adc1 не доказывает применение SQL:

1. `database/20261020_manager_bindings.sql` — только если модуль полностью отсутствует;
2. `database/20261020_analytics_tasks.sql` — только если полностью отсутствует;
3. `database/20261020_analytics_task_policy.sql` — только если полностью отсутствует;
4. **`database/20261022_analytics_budget.sql`** — новая миграция.

Если первые три уже полностью применены и совпадают, их **не повторять**. Если применены частично — STOP, отдельная reviewed reconciliation. Новая миграция создаёт analytics_task_budget, analytics_task_budget_events, analytics_automation_sync_state; начальные defaults закрыты.

DDL MySQL/MariaDB делает implicit commit. Нельзя обещать rollback всей SQL-миграции одной транзакцией. Скрипты не идемпотентны: не запускать повторно и не использовать `--force`. После ошибки сохранить выполненные шаги/DDL-вывод и остановиться; не делать DROP для «исправления». SQL новой миграции проверен на настоящей изолированной MariaDB.

## Точная последовательность согласованного disabled-внедрения

1. **Read-only preflight.** Зафиксировать actual SHA/status/хеши изменяемых файлов/старый image ID+digest/действительную конфигурацию запуска. Сверить schema/ключи/версии и обе расходящиеся ветки. Получить утверждённую пару commit SHA из итогового отчёта/PR, а не только имя изменяемой ветки. Пока сверка не выполнена — STOP.
2. **Backup.** После отдельного разрешения создать private каталог с umask077 вне web-root и Git. Сохранить код с локальными server changes, binary diff, конфигурацию запуска и приватную конфигурацию, текущий image, schema-only и consistent data backup обеих БД. Сохранить SHA256 файлов backup. Не печатать credentials и не добавлять backup в build context/Git. Проверить чтение backup и восстановление на разрешённом disposable стенде. Backup без репетиции восстановления недостаточен.
3. **Зафиксировать окно и остановку.** По отдельному разрешению остановить только analytics/release/consumer расписания/процессы. Подтвердить worker env disabled, consumer disabled, отсутствие active transactions. Не затрагивать импорт/другие службы без согласованного окна. Ни один режим enabled не нужен для disabled-внедрения.
4. **Собрать reviewed candidate.** Из чистого tracked checkout точных согласованных SHA; не из грязного production backend. Dockerfile содержит COPY . . — backend/.analytics_shadow_backup_20261021_110515, .env, выгрузки и другие untracked не должны попасть в образ. Существующий backup на сервере не удалять. Tag образа включает reviewed SHA; старый image/tag/digest сохранить.
5. **Применить PostgreSQL только до точной цели.** В одноразовом контейнере нового reviewed образа с согласованными existing DB network и private env, переопределив CMD/entrypoint, выполнить **`alembic upgrade 20261022_24`**, не произвольный head. Не стартовать обычный backend до этого этапа: его CMD автоматически выполняет Alembic. Перед применением heads нового образа должен быть только 20261022_24; иначе STOP. После — read-only проверить revision, пять таблиц, FK/UNIQUE/index и закрытую policy row.
6. **Применить Calltrack SQL.** В указанном выше порядке только отсутствующие целиком reviewed migrations. Credentials передавать через защищённый client defaults-file/approved secret mechanism, не `-pPASSWORD`. Вести приватный migration journal с SHA256 скриптов, порядком и результатом. После — проверить InnoDB, ограничения, budget row NULL/false/0, обе независимые sync_state. Не переносить shadow cursor в approved.
7. **Развернуть disabled-код.** Сначала Sales backend, затем Calltrack reviewed release с сохранением его server-only функций/приватных файлов согласно preflight. Обычный backend CMD может проверить head; на уже проверенной revision24 это должен быть no-op. Не запускать worker/release/consumer/cron и не менять источники. Не менять production ветки слепым pull.
8. **Контроль после deploy.** Health, actual image SHA, schema24, PHP syntax, прежние read APIs, SHADOW latest/false, approved endpoint закрыт (503 при disabled), consumer disabled даёт zero requests. API health/read запросы могут писать штатный access audit; это отдельная post-deploy smoke-проверка, не строгая READ-ONLY SQL-сверка. Проверить initial policies/budget и отсутствие неожиданных approved events/tasks. Ненулевые неожиданные данные — STOP, не «обнулить».
9. **Остановиться.** Disabled-внедрение не разрешает реальные задачи. Перед pilot нужны все согласования ниже и отдельное явное разрешение владельца. Настройка production signer/provider, protected env, approvals, budget и расписаний — отдельный этап.

Не приводятся выдуманные project/network/secret-file пути или пароли для migration Docker-команды: их фиксируют по preflight и private run sheet перед разрешением шага 5. Обязательные target revisions/SQL и порядок выше точны; execution binding к серверу сейчас NOT VERIFIED.

### Backup-команды (только будущее согласованное окно)

PostgreSQL consistent dump, credentials читаются внутри существующего db-контейнера, вывод только в protected файл:

```sh
# Из server Bash, только после выбора приватного BACKUP_DIR владельцем.
umask 077
docker exec sales-journal-db sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$BACKUP_DIR/sales.dump"
```

Calltrack: `mariadb-dump --defaults-extra-file=PRIVATE_APPROVED_FILE --single-transaction --routines --triggers --events --hex-blob CALLTRACK_DATABASE > protected-backup.sql`. Это шаблон будущего окна, PRIVATE_APPROVED_FILE/имя БД должен определить владелец, не публиковать их содержимое. Для consistent dump все рабочие таблицы должны быть InnoDB; DDL на время backup запрещён. Проверить code/config/image backup и sha256 отдельно; восстанавливать на production в этой задаче запрещено.

## Откат без потери истории

1. Остановить consumer/release scheduler и поставить consumer disabled; отозвать task policy/budget/producer policy после отдельного разрешения. Дождаться текущей транзакции; уже committed task не исчезает при смене флага. Сверить новые задачи и lifetime used_tasks.
2. Сохранить все releases/events/decisions, inbox/checkpoints/bindings, tasks/results/history и budget counters. Не сбрасывать sequence, source_signal_key или used_tasks. Не делать bulk cancel/reassignment.
3. Вернуть старый reviewed Calltrack code/image **при disabled consumer**. Старый consumer не знает нового budget gate; его запуск enabled при откате запрещён.
4. Вернуть старый Sales image, сохранив additive schema24. **Важный риск:** старый Alembic-код знает только revision23, поэтому его стандартный `alembic upgrade head && uvicorn…` может не стартовать на revision24. Для этого rollback нужен отдельно reviewed launch override, запускающий только прежний uvicorn, без Alembic:

```yaml
# Содержимое будущего временного approved rollback override, не текущая production-конфигурация.
services:
  backend:
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
```

Сверить ENTRYPOINT старого сохранённого образа; если он тоже запускает Alembic, command override недостаточен — STOP и отдельный reviewed entrypoint override. Репетиция старого образа с additive schema24 обязательна до deploy. Не делать alembic stamp23 и не запускать destructive downgrade ради старого startup.

5. Health, прежние read APIs, disabled/SHADOW, read-only schema/cursors/budget и задачи сверить с before/after. Если откатываются именно неуспешные MySQL DDL, не угадывать частичный rollback: отдельная reviewed remediation/restore plan. Downgrade24 удаляет approved-историю; здесь он не является штатным production rollback.
6. Повторное включение — только после устранения причины, нового source evidence/согласования и повторной проверки, не replay устаревших payload.

## Что владелец должен утвердить перед первым automatic task

| Параметр/решение | Текущее состояние |
|---|---|
| Scope | Только Авиаторов; ClientsVR Оптовик/Корпоративный; policy /2; CHURN_RISK only |
| Producer min_confidence | Не утверждён; нет default |
| Consumer min_confidence и допустимая разница с producer | Не утверждены |
| CHURN_RISK priority и calendar due_days/UTC deadline | Не утверждены; тестовые HIGH/1 не production |
| Lifetime max_tasks, кто вправе повысить cap/отозвать approval | Не утверждён; 10 лишь предложение; max=NULL/approved=0 |
| Required sources, issuer и владелец независимого подтверждения | Не предоставлены |
| Signer provisioning/разделение прав, rotation/revocation | Не утверждены; key пуст |
| Manifest max age, valid_until, source/client evidence TTL, max signal age | Не утверждены |
| Reviewed client_id, полная история/алиасы, expected manager_id | Реальная проверка не выполнена |
| Периодичность worker, manifest/release и consumer; ответственное дежурство | Не утверждены |
| Stop conditions/допустимые latency/error/backlog пороги и время реакции | Не утверждены; ошибки identity/дубли/budget/source mismatch означают немедленный stop |
| Replay/re-evaluation после budget exhaustion, 410, смены менеджера/источника | Не утверждён; не сбрасывать cursor/inbox; только fresh re-evaluation и приватный review |
| Приёмка первой партии и проверка completion, отчётности | Не назначены |

Не разрешать PURCHASE_OVERDUE/RFM/revenue-drop через policy: они вне первого этапа и жёстко блокируются кодом.

## Точные блокеры первого запуска

1. Нет независимого достоверного source manifest/provider/реестра обязательных upstream sources; локальные metadata/COUNT не устраняют UNKNOWN.
2. Нет утверждённой business policy и pilot budget; нет production signer/proof TTL/отзыва.
3. Не проверены реальные stable IDs, весь исторический sales scope, алиасы и Calltrack manager mappings. Совпадение имени/телефона доказательством не является.
4. Не получена свежая server сверка: расходящаяся Calltrack ветка, actual image/schema/scheduler/private конфигурация.
5. Нет production approval на миграции, deploy, diagnostic writes и отдельное включение реальных задач. Успешный синтетический E2E не заменяет эти решения.
6. Не отрепетирован rollback именно сохранённого production image со schema24. Это gate внедрения, а не обнаруженный сбой текущего E2E.
