# Историческая сверка полноты импорта

## Причина и область изменения

До исправления `services/import_control.py:check_date()` отказывал при
`day < import_control_config.start_date`, а окончательный пропуск требовал
завершённого FTP-цикла. `routers/import_control.py:/check` ограничивался31 днём;
`/check-open` перебирал только уже найденные проблемы. Поэтому файл, который
вообще не поступал, за раннюю дату не становился проблемой. Отдельного курсора
многолетней сверки не было. Это вывод по коду, не аудит production PostgreSQL.

Теперь существуют два режима одного алгоритма определения ожидаемого имени и
оценки ImportBatch/ImportReceipt:

- Ежедневный: start_date — **дата включения ежедневного контроля**, прежние
  enabled/automatic/email и срок00:20 МСК следующего дня сохранены.
- Исторический: начало каждого подразделения определяется MIN(start_date) его
  правил. Глобальная дата включения/FTP cycles не отсекают ранние годы.
  Применяется только активное/контролируемое правило на конкретную дату,
  включая end_date; закрытие магазина не создаёт ожиданий после этого дня.
  До срока и при незавершённой обработке статус waiting, пропуск не создаётся.

Даты/названия магазинов из ТЗ не внесены в код/миграцию. Проверки конкретных дат
2019-09-21,2000-11-11,2025-04-26,2026-05-29 находятся только в синтетических тестах.
Форматы имён, EROOR_, нормализация, подтверждение периода/ошибок/нулевых продаж и
защита повторного импорта сохраняют прежнюю логику. Успех без достоверного периода
не считается подтверждённым файлом. Реестр и продажи не удаляются/не заменяются.

## Хранение и выполнение

Явная миграция **20261109_26 → 20261111_27** добавляет только:

- `import_history_job`: один запрос администратора, план по подразделениям,
  следующий день/позиция, счётчики, fingerprint правил, состояние и timestamps.
  По умолчанию paused, при установке строка задания вообще не создаётся.
- `import_history_results`: PK(дата,подразделение), правило, ожидаемое имя,
  success/missing/error/unavailable/waiting/not_expected, код причины, checked_at.
  FK RESTRICT; индексы status/expected_filename/(department_id,sales_date). Таблица хранит небольшие метаданные,
  не исходные файлы, изображения или продажи.

Общая история пропусков — прежняя `import_control_issues`, UNIQUE(дата,подразделение).
Результаты/issue/курсор одного дня коммитятся **одной транзакцией**. После прерывания
последняя незакоммиченная дата повторяется. Повторная сверка обновляет существующие
строки, не создаёт дубли. История административных закрытий сохраняется. Успешный
поздний импорт переводит открытую проблему в resolved («Загружен» в результатах).

Отдельного веб-сервиса, таймера или FastAPI BackgroundTasks нет. Существующий
минутный maintenance FTP scheduler выполняет только ранее одобренное задание,
даже если ежедневный automatic выключен. При первом старте/деплое без задания
никакая история не запускается. Сохранённое running-задание продолжится после
перезапуска; paused не продолжится до команды администратора. Работает один
логический курсор: nonblocking PostgreSQL CONTROL_LOCK734190270 сериализует
проверяющие процессы с текущим контролем/изменением справочника. Нет долгой
блокировки на весь многолетний проход.

По умолчанию25 пар «подразделение-день» за один maintenance tick, бюджет3секунды;
проверка RAM через существующий ensure_headroom(256МиБ), statement_timeout2s,
lock_timeout100ms. Транзакция/таймаут ограничены одним днём; при отказе ресурсов
задание приостанавливается с сохранённым курсором. Нет быстрых бесконечных retries.
В каждом tick читается ограниченный справочник правил (максимум10000), а не вся
история продаж. Evidence по имени ограничены1000 legacy/5000 receipts; превышение
останавливает задание и не выдаётся за успешную проверку.

`IMPORT_HISTORY_BATCH_SIZE`(1–100,default25) и
`IMPORT_HISTORY_BATCH_SECONDS`(0.1–10,default3) — дополнительные необязательные env.
Менять production overrides ради них не требуется. Дольше/больше по умолчанию
не установлено. Проверки текущего импорта остаются в прежнем scheduler.

Прогресс — обработанные календарные дни подразделений, **не количество файлов**.
Он включает промежутки без правила; проценты по ID/upper_id не используются.
План фиксируется на дату явного запуска. Новые дни обслуживаются ежедневным
контролем; новый полный исторический пересчёт выполняется явно. Новое/изменённое
правило меняет fingerprint и приостанавливает старый план. Нужен новый запуск,
а не тихое продолжение устаревшего снимка. При переносе первого правила вперёд
новая полная сверка также пересматривает ранее проверенные дни, помечает их
not_expected и закрывает соответствующие открытые пропуски с причиной
rule_no_longer_applies, не удаляя историю.

Исторический код не создаёт ImportNotice и не отправляет email. Периодическое
обновление прежних открытых issues не рассылает исторические проблемы. Обычные
уведомления сегодняшнего ежедневного цикла сохраняются. receipt_after повторно
проверяет до31 исторической строки точного имени; оставшиеся открытые проблемы
переоцениваются малыми порциями. Waiting-даты повторно проверяются после00:20,
даже если ежедневный контроль не включён.

## Интерфейс и API

В «Импорт → Контроль импорта → Историческая сверка»:

- Выбор всех/одного подразделения; пустое поле «с» означает с его первого правила.
- Отдельный флаг подтверждения, запуск, пауза, продолжение и обновление прогресса.
- Таблица кодов/начала истории/плана, результаты по датам и ожидаемым файлам.
- Фильтры подразделения, периода, статуса; серверная пагинация50 записей.
- Статусы «Загружен», «Не загружен», «Ошибка импорта», «Требует проверки».
- Пропуски появляются в общей истории сразу после коммита соответствующей даты.

Все маршруты `/api/import-control` используют прежний server-side
`require_settings_admin`; просмотр/изменение не открыты прямым HTTP в обход UI.

| Метод/путь после /api/import-control | Назначение |
|---|---|
|GET /historical/status|Сохранённое задание/план/прогресс|
|POST /historical/start|approve=true; optional department_id,date_from,date_to; новый план|
|POST /historical/pause|Остановка после текущей атомарной даты|
|POST /historical/resume|Продолжение, если правила не изменились|
|GET /historical/results|department_id,date_from,date_to,status,page|
|GET /template-repair/preview|Только предложения без записи|
|POST /template-repair/apply|approve,token,rule_ids,reason; атомарная корректировка|

## Исторические шаблоны: сначала отчёт, потом подтверждение

Фиксированное имя `ReestrRN_Bahturova_09.11.2026.html` не эквивалентно
`ReestrRN_Bahturova_{date}.html`. Оно не должно молча давать один и тот же файл за
каждый исторический день. Если обнаружен проверяемый календарный суффикс, до
исправления показывается unavailable/historical_template_requires_review.

1. Приостановить задание, если оно выполняется.
2. Нажать «Подготовить отчёт исправления шаблонов». Сервер возвращает rule_id,
   код, неизменяемые границы, старое/предлагаемое имя и token всего снимка правил.
3. Администратор сверяет шаблоны с1С. Никакие12 названий автоматически не утверждаются.
4. Выбрать правила, указать причину и отдельное подтверждение, применить.
5. Сервер повторно проверяет token под CONTROL_LOCK. При изменённом справочнике
   отказ409 и новый preview. Сохраняются start/end/id и история всех смен в audit.
   Существующий PostgreSQL trigger запрещает пересечения/конфликты имён; при
   одном конфликте откатывается **весь** набор исправлений.
6. Начать **новую** сверку с первого правила, сначала для одного магазина/малого
   периода. Старый job не возобновлять после смены правил.

Формат даты/исторические даты не угадываются и не меняются коррекцией шаблонов.
Неопознанные суффиксы не исправляются. Это целевое исправление утверждённого
метаданного с аудитом, не обход глобальной start_date.

## Установка и откат — не выполнены

**Отдельный updater26→27 подготовлен в [updater_schema27.md](updater_schema27.md),
но не установлен на FirstVDS.** Исходный updater26 и production-конфигурация не менялись.

**Updater из PR176 поддерживает только цели25/26 и должен отказаться от launcher27.**
Не менять его строки26→27 массово и не запускать --migrate-25-to-26 для этого релиза.
Нужна отдельно согласованная процедура оператора26→27 с той же полной цепочкой
server-only overrides/locks/flock и контролем image/YAML. Рабочий updater здесь
не редактируется и не устанавливается. Готовые образы строить вне FirstVDS.

1. Review/merge PR; проверить внешнюю резервную копию, свободный диск и RAM,
   фактическую revision26, IDs прежних образов, всю Compose-цепочку и restore plan.
   Сохранить закрытые backups конфигурации/образов; не создавать11ГБ dump на тесном VPS.
2. ДоDDL извлечь launcher27 из подготовленного immutable candidate image, сохранить
   SHA256/read-only файл и проверить bridge со старым образом26 в изолированной
   среде. Проверить effective DATABASE_URL/entrypoint, не печатая секреты.
3. Согласовать maintenance, исключить manual writers/импорты/внешние producers,
   сохранить все прежние locks/overrides/churn/Calltrack/mTLS. Автоматизация не включается.
4. Только отдельно одобренный job: `deploy/migrate.import27.explicit.override.yml`
   добавляется последним к **полной** существующей COMPOSE-цепочке. Нужны
   SALES_CANDIDATE_IMAGE с immutable образом и прежние защищённые env. Профиль
   schema-maintenance отключён по умолчанию. Команда для будущего оператора:

   ```bash
   "${COMPOSE[@]}" -f deploy/migrate.import27.explicit.override.yml config --quiet
   "${COMPOSE[@]}" -f deploy/migrate.import27.explicit.override.yml \
     --profile schema-maintenance run --rm --no-deps schema-migrate
   ```

   Это **не dry-run**. Новая миграция добавляет две пустые таблицы; обычный
   запуск приложения не мигрирует. Результат — SCHEMA_MIGRATION_OK expected=20261111_27.
   Проверяется фактическая версия, columns/nullability/PK/FK/CHECK/index/defaultpaused,
   defaults OFF и rule guard прежнего26. Прямой25→27 запрещён.
5. Переключить только backend/затем frontend готовыми образами штатным контролируемым
   способом; health /api/health и /vr/sales/, running image ID=effective YAML image ID,
   command/entrypoint/version. Не пересоздавать DB/volumes. Новые письма/FTP не запускать
   ради smoke; существующие настройки не сбрасываются, job отсутствует.
6. В UI сначала preview, затем только утверждённая коррекция и малый запуск,
   сверка с реестром. Полный многолетний запуск — отдельное разрешение администратора.
7. При failed frontend возвращается **только frontend**. После фактической27 обычный
   старый launcher26 несовместим: использовать заранее проверенный
   `deploy/rollback.import27.checked.override.yml` с pinned старым image и readonly
   SALES_ROLLBACK_LAUNCHER27, --rollback/expected27/lifespanOFF. Таблицы не удалять.
   API при lifespanOFF не становится read-only: оператор блокирует manual writes.
   Восстановить согласованность image/YAML/CMD/mount, health и фактической схемы.
   Если исход неизвестен — сохранять backups/markers, не делать stamp/downgrade/слепой retry.

## Проверки и ограничения

`backend/tests/test_import_history.py`: индивидуальные даты/исчезнувший файл,
старые success/error, late import, idempotency, gaps, pause/resume/restart/cancel,
правила/token/конфликты,00:20, no-mail, нехватка RAM, metadata benchmark,
native26→27/noop/bridge/corruptedFK. Расширены прямые auth-тесты всех новых API.
`frontend/tests/ImportHistoryPanel.test.tsx`: отсутствие start при mount/StrictMode,
один POST, pause/resume, фильтры/пагинация, подтверждение исправлений, error/retry.

Native tests opt-in: IMPORT_CONTROL_TEST_DATABASE_URL только localhost и БД
import_control_test, случайная schema на тест. В production команды не выполнялись.
Конкретные12 правил/их истинные даты, весь фактический реестр, live images, SMTP,
server-only overrides и установленный updater здесь не проверены. Даты из ТЗ —
примеры оператора, не доказательство проверенной базы. Результаты измерения в PR —
на синтетических метаданных, без sales/sale_items и без прогноза production RSS.
Многолетняя сверка может честно обнаружить много старых пропусков/неподтверждённых
периодов; это не повод посылать тысячи писем или автоматически переимпортировать.


## Изменённые файлы

- Backend: `app/services/import_history.py`, `app/services/import_control.py`,
  `app/services/import_control_worker.py`, `app/routers/import_control.py`,
  `app/models/import_control.py`, `app/models/__init__.py`, `app/config.py`.
- Схема/startup: `alembic/versions/20261111_27_import_history.py`, `start_backend.py`.
- Opt-in deploy: `deploy/migrate.import27.explicit.override.yml`,
  `deploy/rollback.import27.checked.override.yml`. Основной Compose не изменён.
- Frontend: `ImportHistoryPanel.tsx`, `ImportControlPanel.tsx`, локальные правила
  `.import-history-panel` в `styles.css`.
- Тесты: `test_import_history.py`, `test_import_control.py`,
  `test_schema25_startup.py`, `ImportHistoryPanel.test.tsx`,
  `tests/browser/import-history-smoke.py`.
- Документация: этот файл и предупреждение в `import_completeness_deployment.md`.

## Результаты локальной проверки

- Широкий backend-прогон:1081 passed,254 skipped (опциональные внешние/PG/browser
  fixtures). Отдельно исключён один cross-repository тест Calltrack: отсутствуют
  `/workspace/Calltrack/api/dashboard_sales.php` и связанный checkout. Тест побайтно
  совпадает с main; отсутствие checkout не связано с изменениями этой ветки.
- Frontend:158 passed,17 test files; TypeScript/Vite build PASS, прежнее
  предупреждение о размере JS chunk сохраняется.
- Настоящий Chromium с полностью подставными API: desktop/mobile390px,
  no-auto-start, подтверждённый start/pause/resume, фильтры/результаты,
  read-only preview/disabled apply, отсутствие JS errors и body overflow — PASS.
- Синтетические9496 дней одного подразделения: одна партия25 дат,475 SQL,
  2620.339мс, peak_traced_bytes2785309 (~2.66МиБ). Это tracemalloc Python allocations,
  **не RSS**, измерение с overhead трассировки. SQL sales/sale_items отсутствуют;
  в таблицах записываются только25 результатов, а не весь план.
- Финальный native/профильный прогон с opt-in Compose config:125 passed,25 skipped.
  Пропущены прежние опциональные native24→25, требующие отдельной БД.
- `git diff --check`, компиляция новых Python-модулей — PASS.

Воспроизведение профильных проверок (только изолированная localhost test DB):

```bash
IMPORT_CONTROL_TEST_DATABASE_URL='postgresql+psycopg://<test>@127.0.0.1:<test-port>/import_control_test' \
SCHEMA27_COMPOSE_TESTS=1 PYTHONPATH=backend python -m pytest -q \
  backend/tests/test_import_history.py backend/tests/test_import_control.py \
  backend/tests/test_schema25_startup.py
```

Фикстура создаёт/удаляет только собственную случайную schema тестовой БД; адрес/имя
production не допускаются. Без специальных env native/Compose тесты пропускаются.
Многолетний тест на production, настоящий FTP/SMTP, build/up/restart production,
замена updater/overrides и production DDL не выполнялись.
