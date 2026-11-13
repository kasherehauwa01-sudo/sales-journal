# Контроль продаж (schema28)

## Источник истины и границы

Контроль использует только `sales.department` и `sales.sale_date`. В действующей модели нет `department_id`: точное название подразделения является естественным ключом. Не объединяем похожие названия и не меняем исторические продажи. Переименование подразделения в исходных данных образует другой ключ; это требует проверки администратором. Подразделения без продаж не включаются. От первой фактической продажи проверяются все календарные дни, включая выходные. Любая существующая строка продажи, в том числе с нулевой/отрицательной суммой, подтверждает наличие продаж; новые правила отмен/выручки не вводятся.

Москва (`Europe/Moscow`), по умолчанию последние 30 завершённых дней; сегодня исключён. Один запрос ограничен 366 днями, 1000 подразделениями и страницей 50 строк. Любую более раннюю историю можно просматривать отдельными диапазонами. Закрытый магазин продолжает участвовать по этому принципу: ожидаемые правила файлов и ручные даты открытия/закрытия не используются. Скрытие/возврат — только метаданные `(department, sales_date)` с аудитом; продажи не удаляются. Поздние продажи сразу исключают строку из живого результата, удаление продажи вновь обнаруживает пропуск, сохранённое исключение действует до восстановления.

Результаты не сохраняются в таблицу пропусков и не кешируются. Count и страница читаются в одном REPEATABLE READ READ ONLY снимке. Исключения/настройки не меняют продажи и не запускают импорт/RFM.

## Карта зависимостей и удаление прежнего контроля

- `routers/import_control.py`, ImportControlPanel, ImportHistoryPanel и их исключительные тесты удалены. Старые HTTP-маршруты возвращают 404. Старые проверки ожидаемых файлов, исторические задания и старые письма больше не планируются.
- `services/import_integrity.py`: сохранены receipt/fingerprint, правила безопасного исправленного файла, ownership/cancellation, FTP deadline/cycle и безопасная классификация SMTP-ошибок. `imports.py` и `ftp_autoload.py` используют этот общий сервис. Старые конфигурации читаются только там, где они уже нужны безопасному импорту, не для нового контроля продаж.
- `import_control.py`: минимальные receipt/filename/lock helpers. `import_control_worker.py` и пустой `import_history.py` оставлены как совместимые import-shims для проверенных старых deployment preflight; старые рабочие процессы не запускаются.
- `background_workers.py`: сохранены FTP, сценарии, RFM/coalescing PR181; добавлен короткий тик уведомлений. Нового сервиса/планировщика нет.
- Старые таблицы правил, истории и уведомлений остаются для аудита/maintenance rollback. Миграция не удаляет их и не правит настройки FTP/SMTP/Calltrack.

## SQL и память

Обязателен составной индекс `ix_sales_department_date_control (department, sale_date)`. Действующие одиночные индексы не заменяют его. Перечень подразделений получается loose index scan, первая дата — LIMIT 1 по индексу; календарная сетка проверяется ограниченными LATERAL LIMIT 1 probes. Не читаем файлы или многомиллионные продажи в Python. В обычном ответе максимум 50 записей, в письме — один день/не более 1000 подразделений. statement_timeout 5 секунд, lock_timeout 1 секунда; для ограниченной проверки SET LOCAL jit=off исключает ненужную LLVM-компиляцию сложного плана (только текущая транзакция, без глобальных настроек); при недостоверной/недоступной проверке — ошибка, а не ложное «всё полно».

Индекс создаётся **отдельно, явно и CONCURRENTLY** перед миграцией. Index-only --create также получает все шесть advisory locks updater и schema lock одной PostgreSQL-сессией, без ожидания; занятый ресурс означает отказ до построения. Для операции устанавливаются session-only maintenance_work_mem=16MB и max_parallel_maintenance_workers=0, проверяется существующий ensure_headroom(). 16MB — рабочая память операции, не общий лимит RSS PostgreSQL; нужен запас для backend/shared buffers/cache. Это чтение всей таблицы на PostgreSQL, потребует диска/IO; запускать только в согласованное окно. Startup и Alembic не строят его автоматически. Незавершённый/неправильный индекс не удаляется автоматически: команда блокируется и требует аудита. Нет повышения cache/RAM/TTL лимитов.

## Метаданные и уведомления

Миграция `20261112_28_sales_control.py` (parent `20261111_27`) добавляет только четыре небольшие таблицы: settings, exclusions, audit, deliveries. PK/UQ/CHECK/FK проверяются launcher28; индекс продаж проверяется на valid/ready/поля. По умолчанию enabled=false, recipients=[], not_before=NULL. Обычный старт только проверяет схему; миграция — отдельная явная команда/updater-флаг.

Сохранение формы не проверяет продажи и не отправляет письмо. `not_before` устанавливается на следующее будущее выбранное московское время; изменения действуют после сохранения. Email валидируются/дедуплицируются. SMTP используется существующий, секретов во frontend/новых таблицах нет. Несохранённый черновик защищён подтверждением перехода, ошибки не уничтожают введённые данные.

Тик обрабатывает только последнюю наступившую ежедневную дату (день до московского расписания), без рассылки накопленной истории после простоя. Нет пропусков — `not_required`, письма нет. Есть пропуски — одно сводное письмо по дате и нормализованной группе получателей. Исключённые строки не попадают в письмо. Заголовки/HTML экранируются. Настройки и исключения блокируются на время отправки, изменения получают 409 вместо гонки.

MAIL→CONTROL PostgreSQL session advisory locks удерживаются одним соединением; транзакции короткие, SMTP вне транзакции. Перед SMTP фиксируется `sending`. После падения процесса незавершённое `sending` становится `uncertain`: SMTP не поддерживает exactly-once и повтор мог бы задублировать уже доставленное письмо. Автоповтор только для определённо временных безопасных ошибок, максимум три попытки с паузой 5 минут; неизвестное подтверждение — только ручной подтверждённый повтор администратора (возможен дубль, UI предупреждает). Изменённая группа получателей требует нового решения, не использует устаревшую очередь. Настройки по умолчанию выключены, ни установка, ни открытие страницы не включают рассылку.

API `/api/sales-control`: GET `missing`, `departments`, `notification-settings`, `notification-status`; PUT `notification-settings`; POST/DELETE `exclusions` с approve; POST `notifications/{id}/retry` с approve. Все маршруты защищены существующим settings-admin. Аудит actor использует проверенную mTLS-прокси идентичность либо существующую shared-session, не выдуманный персональный login.

## Подготовка и установка (выполняет оператор, не Codex)

1. Проверить backup/свободный диск/память, отсутствие активной исторической сверки; старое задание должно быть остановлено штатно до обновления. Не сбрасывать курсоры/таблицы. Зафиксировать старые immutable image ID, полную Compose-цепочку и checkpoints. Сборка новых backend/frontend только вне FirstVDS.
2. Передать готовые образы/проверить SHA и `docker load` штатной процедурой. Не применять updater27 к schema28. Сохранить действующий updater27.
3. Генераторы принимают только проверенный внешний updater26 с SHA256 `78295c93abcc9c702b1028c41eafb552c94869483f83c99cb9bebe1bdc58e0d8`. На машине подготовки:

```bash
python3 tools/prepare_schema28_updater.py /path/to/reviewed-update26.sh /new/path/update_sales.schema28.sh
python3 tools/prepare_schema28_recovery.py /path/to/reviewed-update26.sh /new/path/recover_sales.schema28.sh
bash -n /new/path/update_sales.schema28.sh
bash -n /new/path/recover_sales.schema28.sh
shellcheck /new/path/update_sales.schema28.sh /new/path/recover_sales.schema28.sh
cd /new/path
sha256sum -c update_sales.schema28.sh.sha256
sha256sum -c recover_sales.schema28.sh.sha256
```

Генератор не перезаписывает вход/существующий выход. Установить **отдельными именами**, не заменяя рабочий серверный скрипт. Сохранены порядок production overrides, Calltrack/churn/mTLS/report-fix, mounts, общий flock и шесть advisory locks исходного updater.

4. Подготовить индекс новым backend-образом **до** миграции, используя полную действующую Compose-цепочку и отдельный одноразовый контейнер с новым image, существующими DB env/networks; команда `python -m app.sales_control_index --create`. Не запускать lifespan/worker/импорт. Точную Compose-команду согласовать с действующими server-only файлами: они не доступны в репозитории. Без `--create` команда только проверяет. Не отключать resource guards ради индекса. Проверить размер/valid/ready индекса и оставшийся диск.
Пример отдельного index-only контейнера для **проверенной в updater Compose-цепочки** (сначала сверить с фактическим сервером; при дополнительных overrides остановиться и добавить их в прежнем порядке):

```bash
cd /var/www/html/vr/sales
umask 077
INDEX_OVERRIDE=$(mktemp /run/sales-index28.XXXXXXXX.json)
python3 - "$INDEX_OVERRIDE" 'sha256:<verified-backend28-id>' <<'PY_INDEX'
import json, re, sys
assert re.fullmatch(r"sha256:[a-f0-9]{64}", sys.argv[2])
with open(sys.argv[1], "w") as f:
    json.dump({"services": {"backend": {"image": sys.argv[2]}}}, f)
PY_INDEX
COMPOSE_INDEX=(docker compose
 -f /var/www/html/vr/sales/docker-compose.yml
 -f /var/www/html/vr/sales/docker-compose.override.yml
 -f /var/www/html/vr/sales/docker-compose.churn-v3u.override.yml
 -f /etc/calltrack/churn-identity/sales-bindings.compose.json
 -f /var/www/html/vr/sales/docker-compose.report-fix.override.yml
 -f "$INDEX_OVERRIDE")
"${COMPOSE_INDEX[@]}" config --quiet
"${COMPOSE_INDEX[@]}" run --rm --no-deps --pull never --entrypoint python backend -m app.sales_control_index --create
"${COMPOSE_INDEX[@]}" run --rm --no-deps --pull never --entrypoint python backend -m app.sales_control_index
# Только после обоих SALES_CONTROL_INDEX_OK:
rm -- "$INDEX_OVERRIDE"
```

Этот run не публикует порты, не запускает зависимости/uvicorn/lifespan и не меняет образ работающего backend. Не применять при активном recovery marker/override. Если команда завершилась неуспешно, не переходить к миграции; временный index-only override можно сохранить для диагностики, он не входит в обычную Compose-цепочку. Не удалять невалидный индекс автоматически.

5. Указать подготовленные **immutable** образы через существующие переменные updater, проверить их локальное наличие:

```bash
sudo env SALES_UPDATE_BACKEND_IMAGE='sha256:<verified-backend28-id>' \
 SALES_UPDATE_FRONTEND_IMAGE='sha256:<verified-frontend28-id>' \
 bash /var/www/html/vr/update_sales.schema28.sh --migrate-27-to-28
```

Updater проверяет pins launcher/миграции, import-preflight, schema27/index, совместимость bridge28, защищённые настройки; блокирует фоновые операции/останавливает старый backend перед DDL. Миграция выполняется единожды; повтор после сбоя проверяет фактическую схему. На уже28 обычное обновление без миграционного флага. Сохранить checkpoints/backup/старые образы. Никакого Alembic downgrade/stamp.

6. Проверить `/api/health`, `/vr/sales/`, фактические image ID/effective Compose/entrypoint, schema28, работу FTP/журнала/RFM, четыре вкладки, реальные пропуски, hide/restore. Рассылка должна оставаться выключенной. Настроить SMTP/получателей/время, проверить диагностически, включить только явным сохранением администратора. На закрытых подразделениях проверить ожидаемую семантику ежедневного контроля.

## Откат и recovery

До миграции сохраняется исходный YAML/image. После commit28 возврат старого native launcher27 запрещён: updater использует старый закреплённый backend image + проверенный bridge launcher28 в maintenance, lifespan OFF. Новые таблицы/индекс остаются. Backend health и image ID проверяются после восстановления; при ошибке резервные файлы/marker сохраняются. Ошибка frontend откатывает только frontend, исправный backend28 остаётся.

Выход из maintenance выполняет **отдельный recover_sales.schema28.sh**, не повтор миграции. Он наследует durable transaction предыдущего recovery, проверяет checkpoint, protected hash с контекстным протоколом, старые и новые immutable ID, schema28/полную effective Compose и все locks. Параметры:

```bash
sudo env SALES_UPDATE_BACKEND_IMAGE='sha256:<verified-backend28-id>' \
 SALES_UPDATE_FRONTEND_IMAGE='sha256:<verified-frontend28-id>' \
 bash /var/www/html/vr/recover_sales.schema28.sh \
 --checkpoint /var/backups/sales-journal/updates/update.<actual> \
 --protected-sha256 '<verified-checkpoint-protected-settings-hash>' \
 --old-backend-id 'sha256:<checkpoint-old-backend-id>' \
 --old-frontend-id 'sha256:<checkpoint-old-frontend-id>'
```

Не извлекать hash поиском произвольной 64-символьной строки, не удалять marker/override вручную. При неудаче backend возвращается исходный maintenance bridge, при ошибке frontend сохраняется уже подтверждённый backend; повтор продолжает проверенное состояние. При неопределённой фазе требуется ручной аудит checkpoint, скрипт отказывает. После успеха recovery-override архивируется, marker снимается последним. Старые таблицы не удалять и старый file-control обратно не включать без отдельного согласования.

## Проверки и оставшиеся ограничения

Native PostgreSQL16-тесты используют отдельные схемы/синтетические продажи, SMTP заменён. Проверяются первая дата/выходные/нулевые суммы, поздние продажи, исключения, пагинация, дефолт OFF, повтор/неопределённая доставка, права, frozen launcher-контракты. Bash-тесты используют подставные Docker/Compose, сигналы, потерю PostgreSQL, неправильные схемы, image drift и независимый frontend rollback. Chromium проверяет desktop/mobile, клавиатуру/подтверждение, отсутствие старых API/переполнения; production не посещается.

Воспроизводимый замер SQL: `SALES_BENCHMARK_DATABASE_URL=<localhost-only-test-DB> PYTHONPATH=backend python tools/benchmark_sales_control.py --rows 1200000`. Создаёт/удаляет только собственную синтетическую схему, выводит три EXPLAIN ANALYZE до/после индекса, BUFFERS и размер индекса. Не использовать production. Этот синтетический тест не воспроизводит production IO, распределение дат/кеш и одновременную нагрузку. Не заявляем устранение всех OOM. PostgreSQL index build остаётся самой тяжёлой разовой операцией; при недостатке диска/ресурсов остановить внедрение, не повышать лимиты вслепую.

## Зафиксированный синтетический замер

PostgreSQL **16.15**, локальный Docker: **1 CPU / 256 MiB**, данные на диске, не tmpfs. 1 200 000 строк, 12 синтетических подразделений, 1000 дат; проверяемая сетка 30 дней. Таблица упрощена до двух используемых полей; это проверка объёма/плана, а не копия production. Одинаковый SQL, session-only JIT off и work_mem=2MB в обеих сериях. Сравнивается наличие составного индекса, **не старый контроль файлов**. Кеш страниц прогрет, реальные холодные IO не воспроизводятся.

| Показатель | Без составного индекса | С индексом |
|---|---:|---:|
| Execution Time, три запуска, мс | 39000,324 / 33184,102 / 34151,956 | 1,384 / 1,220 / 1,253 |
| Shared Hit Blocks, последний запуск | 2 566 795 | 1 159 |
| Пиковый RSS Python-клиента, MiB | 66,59 | 66,59 |
| Memory contexts PG после запроса, байт | 1 952 728 | 2 017 424 |

Индекс: **9 003 008 байт (8,59 MiB)**, CONCURRENTLY build **2601 мс**, maintenance_work_mem=16MB, parallel maintenance workers=0. Полные планы: [benchmarks/sales_control.postgres16.json](benchmarks/sales_control.postgres16.json). RSS клиента не включает сервер; memory contexts после запроса **не являются пиковым RSS PostgreSQL**. Cgroup peak всего стенда за тесты и замер достиг 256 MiB (включая cache), `oom=0`, `oom_kill=0`; это не обещание такого же пика на VPS с другими контейнерами.

При первой попытке в другом стенде с PGDATA в tmpfs лимит 256 MiB включал сами файлы/WAL: возник OOM, результат замера отклонён. Для актуального замера данные перенесены на диск **без повышения memory limit**. Оператору всё равно необходимо оценить свободный диск/WAL/IO и память на FirstVDS перед index preparation; синтетические 2,6 секунды нельзя использовать как production SLA.
