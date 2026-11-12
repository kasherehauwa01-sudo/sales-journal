# Фоновое обслуживание, историческая сверка и RFM — schema27

## Аудит и установленная причина

Основание: main `9908c49`, PostgreSQL `20261111_27`. FirstVDS не посещался;
production PID193692 и его Python stack недоступны. Поэтому доказан дефект кода и
воспроизводимый механизм, соответствующий симптомам, **а не фактическое место
ожидания конкретного production PID**. Нет доказательств зависшего FTP-сокета или
PostgreSQL deadlock. Приведённый оператором `pg_blocking_pids={}` не указывает на
блокировку другими SQL-транзакциями.

Цепочка старого кода с привязкой к неизменяемому baseline:

1. [ftp_autoload.scheduler,142–151](https://github.com/kasherehauwa01-sudo/sales-journal/blob/9908c49/backend/app/services/ftp_autoload.py#L142)
   вызывает maintenance прямо внутри ожидания23:55. Следующий проход возможен
   только после возврата всего maintenance и завершения самого FTP-цикла.
2. [maintenance,273–279](https://github.com/kasherehauwa01-sudo/sales-journal/blob/9908c49/backend/app/services/import_control_worker.py#L273)
   последовательно ждёт history, waiting, а затем check_deadline_cycle.
   [check_deadline_cycle,270](https://github.com/kasherehauwa01-sudo/sales-journal/blob/9908c49/backend/app/services/import_control_worker.py#L270)
   может выполнить **целый run_autoload**, не только короткую проверку.
   Таким образом, последний history tick13:19 и продолжавшиеся импорты совместимы
   с исправно работающим, но занятым FTP-циклом, без исключения/сетевого зависания.
3. [_run_autoload_cycle,43–65](https://github.com/kasherehauwa01-sudo/sales-journal/blob/9908c49/backend/app/services/ftp_autoload.py#L43)
   открывает Session, делает SELECT ftp_config и удерживает scope во время listing,
   всех файлов/импортов. При непустом listing commit внешней Session отсутствует.
   SQLAlchemy autobegin оставляет транзакцию и MVCC xmin на всё это время.
   Внутренние commit других Session не завершают эту внешнюю транзакцию.
   Именно этот дефект воспроизводит `idle in transaction / ClientRead / last SELECT
   ftp_config / no advisory locks`. Пул объясняет, почему physical connection могла
   быть создана ещё при startup, раньше самой транзакции.
4. [_process_file,71–90](https://github.com/kasherehauwa01-sudo/sales-journal/blob/9908c49/backend/app/services/ftp_autoload.py#L71)
   дополнительно делает refresh(entry) перед download и refresh(batch) перед
   remote finish. Refresh начинает новую read-транзакцию; ожидание сети происходит
   до её завершения. [receipt_before](https://github.com/kasherehauwa01-sudo/sales-journal/blob/9908c49/backend/app/services/import_control_worker.py#L96)
   также ждёт файловый SHA thread внутри read Session.
5. [process_import,120–126](https://github.com/kasherehauwa01-sudo/sales-journal/blob/9908c49/backend/app/services/imports.py#L120)
   после каждого completed файла **ждёт** полного rebuild_after_sales_update.
   [rebuild_snapshot,59–85](https://github.com/kasherehauwa01-sudo/sales-journal/blob/9908c49/backend/app/services/rfm_snapshot.py#L59)
   coalesce переиспользует только сборку, завершённую после requested_at.
   Следующий последовательный файл запрашивает пересчёт уже после предыдущего
   calculated_at, поэтому coalesce не объединяет последовательный поток файлов.
6. [tied_quintiles](https://github.com/kasherehauwa01-sudo/sales-journal/blob/9908c49/backend/app/services/rfm.py#L25)
   делает list.count для каждого уникального значения: O(N×U), доO(N²).
   Это синхронная CPU-работа внутри event loop и отдельная подтверждённая причина
   задержки **всех** async задач. Полная длительность production RFM также включает
   SQL/подготовку/запись: её состав по исходным логам установить нельзя.

RFM — доказанно последовательный усилитель длительности FTP-цикла и потенциальный
CPU blocker. Не доказано, что конкретный production вызов ожидал именно RFM или
сетевой ответ. Остановка всех tick объясняется архитектурой даже при конечных,
успешных FTP-операциях. Не используется pg_terminate_backend или отмена SQL-сессий
как способ «лечения».

## Исправление

[background_workers.py](../backend/app/services/background_workers.py) запускается
из единственного существующего FastAPI lifespan, без нового контейнера/cron.
Каждая lane вызывает операцию последовательно; собственные invocations не
накладываются. Ошибки одной lane не отменяют остальных. Cancellation проходит
наружу, lifespan завершает все дочерние задачи до dispose engine.

| Lane | Порядок/границы |
|---|---|
| ftp_daily |Прежний scheduler23:55, без смены autoload_timezone|
| scenarios |Прежний hourly scheduler/логика сценариев, не переписаны|
| history |Только уже одобренное running-задание, polling5с, прежние25 дней/3с на batch|
| history_waiting |До5 ожидающих исторических дат, раз в60с; historical без рассылки|
| ftp_deadline |Прежний deadline00:20 и catch-up, раз в60с; тот же FTP_LOCK|
| import_recheck |Прежние ограниченные open/finished-cycle metadata checks, раз в60с|
| import_mail |Прежний outbox/MAIL_LOCK/dedupe/retry policy, раз в60с|
| rfm |Проверка dirty состояния раз в60с или лёгкий wake-up после импорта|

SQL-only metadata lanes имеют15/30с external timeout, cleanup использует штатные
rollback/close. У history остаются прежние SQL2с/lock100мс/batch seconds и атомарный
commit день+результат+позиция. Контроль правил, пауза, resume, locks и отсутствие
автоматического создания job сохранены. `maintenance()` остаётся compatibility
one-shot: операции через gather/safe_hook; обычный scheduler больше его не ждёт.
Нулевые idle history batches не печатаются каждые5с; liveness доступна через API.

FTP/SMTP threads нельзя безопасно оборвать asyncio timeout: сетевой побочный эффект
продолжится в thread. [run_blocking](../backend/app/services/async_resources.py)
shield/drain удерживает ownership до фактического окончания thread, затем повторно
выбрасывает CancelledError. Повторная отмена не освобождает lock преждевременно.
FTP сохраняет socket timeout30с/retries/delay, SMTP — прежний socket timeout и
uncertain/no-blind-retry policy. Искусственного общего тайм-аута на download нет.
При остановке контейнера зависший DNS/длинный поток может потребовать штатного
container termination; это остаётся ограничением, не скрывается успешным rollback.

[ftp_autoload.load_config](../backend/app/services/ftp_autoload.py) возвращает
immutable scalar settings и **закрывает read Session до сети**. Доступы хранятся
только в памяти; dataclass repr их не печатает. Download, process_import, remote
finish и rename не выполняются внутри metadata Session. Записи журнала/batch и
чтение четырёх scalar counters используют отдельные короткие транзакции. Правила
успеха, EROOR_, file cleanup, receipts и fingerprint/reimport guards сохранены.
receipt_before закрывает read Session до SHA и повторно читает batch перед записью.
Ошибки FTP в журнале содержат технический код/класс, а не raw exception с реквизитами.

## RFM: устойчивое объединение без миграции

[rfm_snapshot.py](../backend/app/services/rfm_snapshot.py): прежний public hook
`rebuild_after_sales_update()` теперь только будит worker. `imports.py` не требует
изменений: await возвращается сразу, тяжёлый rebuild из пути импорта исключён.
Callback удаления продаж также становится лёгким; существующий changed(db)
по-прежнему записан с самими изменениями. RFM не выключен.

Durable источник dirty состояния:

- существующая `seasonality_state.source_revision` — завершённые/неуспешные импорты
  и удаления продаж уже меняют её в своих транзакциях;
- `sum(import_batches.added_rows)` — меняется вместе с каждым row commit и поэтому
  ловит частично завершённый импорт после SIGKILL до финального changed();
- `max(import_batches.id)` — новый batch. Это один SQL aggregate по **журналу
  файлов**, не SELECT миллионов sales/sale_items и не загрузка истории в Python.

Подтверждённый signature сохраняется единожды в JSON `rfm_snapshots.boundaries`
под внутренним ключом `_refresh_source`. Существующие r/f/m границы/формулы,
client metrics, таблицы/колонки, immutable generations и active pointer сохранены.
Это **добавочное служебное JSON-поле**, не изменение опубликованных API/score.
Legacy snapshots без него читаются, но считаются нуждающимися в одном обновлении.
Generic settings/job-таблицы в схеме нет; отдельная очередь/миграция не нужна.

Под RFM_LOCK734190222 → SOURCE_LOCK734190220 builder повторно проверяет provenance.
Background locks — try, без ожидания транзакцией активного импорта/другого builder.
Explicit builder сохраняет прежнюю сериализацию; coalesce теперь требует не
только timestamp, но и совпадение source signature, чтобы не скрыть позднюю запись.
SOURCE exclusive до publish сохраняет прежнюю согласованность sales inputs.
Новые продажи, ожидавшие этот lock, после commit изменят signature: следующий
пересчёт останется pending даже после рестарта/потери wake-up Event.

Готовый current signature не пересчитывается. Dirty snapshot пересчитывается не
раньше300с после предыдущего publish: окно не сдвигается при каждом новом импорте,
поэтому непрерывный поток не продлевает debounce бесконечно. Это ограничение частоты,
не требование ждать окончания всех будущих импортов. При длительном FTP-потоке
возможны несколько сборок, но не одна после каждого файла. Idle/source busy не
теряет запрос. Максимальное время актуализации зависит от доступности ресурсов;
5 минут не является обещанием SLA при постоянно занятом SOURCE/нехватке RAM.

Почему300с: обычный RFM-отчёт по фильтрам использует live rfm_rows, не этот snapshot;
immutable snapshot обслуживает client-insights, который возвращает calculated_at,
age_seconds и stale по дате периода. В нём **нет** проверки SLA36ч; setting
analytics_signals_freshness_hours не используется этим потребителем и не служит
основанием выбора интервала. Sub-minute требование к RFM в коде не зафиксировано.
Выбрана нижняя граница предложенного в ТЗ диапазона5–15мин: она объединяет burst,
а фактические прежние rebuild уже занимали200–278с. Это явный компромисс: last-good
RFM может отставать на интервал+poll+build/ожидание ресурсов. Его точный бизнес-SLA
нужно наблюдать/подтвердить при внедрении; никаких новых freshness-политик Calltrack
или TTL отчётов не введено. Существующий stale не становится dirty-флагом при
изменениях внутри текущего дня — для pending смотрите admin workers API. При смене
календарного дня период также считается dirty. Первый отсутствующий/старый snapshot
может построиться после startup, только при свободных locks и достаточной памяти.

Сохраняется общий heavy_operation/headroom256МиБ (без увеличения лимитов). SQL
metadata preflight5с/lock1с; фоновые SQL300с, whole build900с. Ошибка/отмена откатывает
целую новую generation, active pointer остаётся прежним. Retry с backoff до300с,
без быстрого бесконечного цикла. SOURCE может задержать следующий импорт во время
самого build — это прежняя обязательная гарантия согласованности; **завершившийся
импорт больше не ждёт RFM**. Нельзя отключать SOURCE ради ускорения.

Counter заменяет только точный подсчёт частот квинтилей. Порядок значений, ties,
границы/scores/сегменты/Decimal суммы/период не меняются; ALGORITHM_VERSION остаётся1.
Чистый scoring выполняется в одном thread с drain-on-cancel, без копирования rows
и доступа thread к Session, чтобы не занимать event loop. Длительная транзакция RFM
с source locks во время расчёта/публикации **не** устранена: она нужна для atomic
snapshot. Также не переписана legacy scenario transaction, удерживающая xact lock
для единственности отправки; она не соответствует наблюдаемому PID без advisory locks.

## Настройки и диагностика

Новые application defaults (без изменения Compose/.env/FTP/SMTP):

- IMPORT_HISTORY_POLL_SECONDS=5, диапазон1–60; прежние batch25/seconds3 не менялись.
- RFM_REFRESH_MIN_SECONDS=300, диапазон60–900.
- RFM_REFRESH_POLL_SECONDS=60, диапазон1–300.
- RFM_REFRESH_TIMEOUT_SECONDS=900, диапазон60–3600.

`GET /api/import-control/workers` — в существующем admin-protected router.
Неавторизованный прямой запрос возвращает401. workers содержит last start/success,
processed, duration_ms, error_code и state каждой **текущей process lane**.
rfm содержит durable current/pending, calculated_at/duration_ms из БД, rebuilding
по фактическому RFM advisory lock; error добавляется из локального worker. Для
нескольких Uvicorn workers liveness/error process-local и сбрасываются после
рестарта; job.checked/position и RFM provenance/active pointer в PostgreSQL
остаются источником истины. «Успешная проверка lane» не равна «новый snapshot»:
для последнего смотрите calculated_at. Значения фильтров/клиентов/пароли не возвращаются.

Endpoint можно посмотреть в браузере с уже авторизованной settings-session.
Не передавать admin cookie/секреты в чат/логи. Новых email и исторических заданий
диагностика не создаёт. Логи содержат batch.checked/duration или технический
класс ошибки, UUID snapshot/число клиентов/длительность. Idle ticks не шумят.

## Проверки и замеры

`test_background_maintenance.py` содержит unit+native PG16/schema27:

- Реальный supervisor продолжает history при заблокированных FTP/RFM/mail;
  без одобрения job не создаётся. Native job одного synthetic отдела за09.11.2026
  завершается1/1 за тестовый timeout2с (test polling10мс, **не production SLA**), без email.
- Native воспроизведение legacy SELECT ftp_config + network wait даёт один
  idle-in-transaction/xmin; исправленный actual FTP cycle — ноль. Проекция counters
  и file download/import/remote finish также не удерживают metadata read transactions.
- Последовательные импорты объединяются; partial batch, lost Event/restart,
  поздняя запись во время RFM, cross-connection single-flight, отмена и сохранность
  прошлого snapshot/освобождение locks. Timestamp coalesce не скрывает новый source.
- Timeout/backoff/неперекрытие lane, повторная отмена настоящего thread, safe logs,
  admin-доступ. Старые receipt/FTP/mail/duplicate/пауза/resume/точность RFM-тесты сохранены.

Широкий backend suite: **1185 passed,267 skipped**,133.17с; один прежний warning
Starlette/AnyIO. Skips относятся к opt-in окружениям/артефактам. Отдельно исключён
`test_calltrack_dashboard_contract.py`: нет checkout `/workspace/Calltrack`; файл
побайтно совпадает с baseline, новая логика его не затрагивает. Проверки настоящего
Calltrack/FTP/SMTP/API production не проводились. Frontend не менялся.

Профиль до последних диагностических additions:161 passed/2 Compose opt-in skipped;
после включения Compose и новых one-day/auth тестов:166 passed/0 skipped,91.22с.
Финальные targeted результаты после coalesce guard приведены в PR. Compileall и
`git diff --check` — PASS. Все DB URLs ограничены localhost test БД/schema.

Воспроизводимый pure-scoring benchmark, **не production нагрузка**:

```bash
.venv/bin/python tools/benchmark_rfm_refresh.py --baseline-ref 9908c49 --clients 40400
```

| На одинаковых40 400 строках | До | После |
|---|---:|---:|
| Scoring, с tracemalloc |18255.666мс|1194.935мс|
| Scoring peak traced allocations |23559472байт|23562952байт|
| Process peak RSS |99155968байт|99295232байт|
| SHA256 полного результата |`82bbf68fb3938f82c74a5a84ea374e9f8288725d343138ece95a66fe3ebe5c77`|тот же|

Ускорение **этого CPU-этапа** ~15.3×. RSS-разница139264байта (~0.13МиБ), traced
разница3480байт. Отдельные child processes; RSS включает генерацию и контрольную
JSON-сериализацию результата, tracemalloc — только scoring. Это не оценка пикового
RSS реального40тыс.-клиентного SQL builder и не обещание15× для end-to-end RFM.
Production SQL, задержки диска/других сервисов и actual aggregate по import_batches
не измерялись. В новом тестовом burst три изменения обнаружены одним rebuild; после поздней новой
записи выполняется следующая сборка. Прежний per-file hook запрашивал бы три rebuild
по своей последовательной цепочке: это вывод из кода, не отдельный production
throughput-замер. Формулы в сравнительном benchmark идентичны.

## Развёртывание оператором и откат — здесь не выполнялись

1. Review/merge PR, подготовить **только backend image** вне FirstVDS, закрепить
   immutable ID/commit/provenance. Frontend/DB схема/launcher/миграции не меняются.
   Проверить schema27, normal mode, отсутствие recovery barriers, здоровые текущие
   контейнеры, доступное место/RAM, сохранить прежние image IDs и existing backups.
2. Выбрать окно без активного FTP/RFM build. Заранее поставить массовую историю на
   паузу, если оператор не хочет её продолжения сразу после startup. Running approved
   job продолжится с сохранённой позиции; paused не запускается сам. Не пересоздавать
   production job1/курсор и не выполнять переимпорт ради smoke.
3. Доставить prepared image прежним способом, **не Docker build на VPS**. Обычный
   проверенный updater27 в режиме already27, **без migration flags**. Пример только
   после отдельного согласования установки, с настоящим локальным immutable ID:

   ```bash
   sudo env SALES_UPDATE_BACKEND_IMAGE='<reviewed local sha256:backend image ID>' \
     /var/www/html/vr/update_sales.schema27.fixed.sh --backend-only
   ```

   Использовать действительно установленную проверенную версию updater с полной
   живой цепочкой overrides. Не заменять её этим PR и не удалять markers, если они
   найдены. Шесть updater locks сохраняются; SOURCE исключает активный RFM.
4. После успешных image/health/schema проверок наблюдать workers endpoint и
   `import_history batch`, `RFM rebuild success`, `background lane=...` logs.
   Один день09.11.2026 существующего отдела может завершиться с missing/error/
   unavailable, если таковы доказательства импорта; это не повод перепровести продажи.
   Для paused job проверить error/rules и пользоваться обычной ручной Resume.
5. Наблюдать один штатный FTP-цикл и RFM pending→rebuilding→current; не инициировать
   массовую сверку/реальные письма/FTP-проходы ради теста. Подтвердить прежние FTP/SMTP
   settings и работу клиентских отчётов. В PostgreSQL можно смотреть только безопасные
   поля активности, без raw query/персональных данных:

   ```sql
   SELECT pid,state,now()-xact_start AS xact_age,backend_xmin,wait_event_type,wait_event
   FROM pg_stat_activity
   WHERE datname=current_database() AND state='idle in transaction'
     AND pid<>pg_backend_pid();
   ```

   Многоминутного metadata SELECT ftp_config, ждущего сеть, больше быть не должно.
   Не завершать сессии автоматически. Сравнивать timings реальных rebuild после
   установки наблюдением, не запуском дополнительной нагрузки.

Откат: schema27 не менялась, downgrade/stamp/DDL не требуется. Сохранённый прежний
native-schema27 backend image совместим; его code rollback выполняется тем же
проверенным backend-only updater с прежним immutable ID, при здоровом normal mode
и без barriers. JSON `_refresh_source` старый код игнорирует, active snapshot
остаётся доступным. Старый код вернёт и прежнюю проблему частого RFM: откат временный.
Frontend отдельно не требуется откатывать.

Если updater сам вернул maintenance bridge/marker или переключение не подтверждено,
**не** запускать обычный updater/compose up вручную и не удалять защиты. Сохранить
checkpoint/IDs и использовать [проверенную schema27 recovery процедуру](schema27_recovery_exit.md)
с фактическими артефактами и отдельным согласованием. Никакой новой миграции26→27.

Оставшиеся риски: реально зависший DNS/FTP-thread не прерывается мгновенно; SQL/RFM
transaction намеренно держит source consistency; постоянная загрузка/мало RAM
откладывают пересчёт, pending остаётся durable. Внешние SQL writers, не использующие
существующий source/changed протокол, не поддерживаются. Snapshot retention не
меняется и старые поколения не удаляются — свободный диск нужно наблюдать.
Сценарии HoReCa сохраняют прежнюю необходимую transaction/отправочную сериализацию.
Данные реального отдела5/правила/production stack не проверялись удалённо.

Финальная проверка после source-signature coalesce guard и RFM rebuilding/error
диагностики: **23 passed**,13.02с (`test_background_maintenance.py` +
`test_rfm_snapshot.py`). Это повторный focused прогон, не23 дополнительных уникальных
теста к широкому suite. Native history/FTP/RFM проверены только на синтетических
данных. Локальный тестовый PostgreSQL останавливается после проверки, без удаления
контейнера/данных; production не подключался.
