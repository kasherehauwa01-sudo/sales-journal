# FirstVDS: prepared images, явная миграция25→26 и code rollback

## Основание аудита

Проверен целиком **прикреплённый оператором** актуальный `update_sales.sh`.
Не использованы прежние512/499-строчные версии или восстановленный по документации
скрипт. Production не посещался; рабочий файл `/var/www/html/vr/update_sales.sh`
не заменялся. Указанный каталог приложения — `/var/www/html/vr/sales`.

| Артефакт | Байты | Строки LF | SHA256 |
|---|---:|---:|---|
| Входной production `update_sales.sh` |52611|915|`09673fad4822ea9287e7839738549e05d289e21f0444d439df5876901c47099d`|
| Подготовленный `update_sales.schema26.sh` |57992|976|`78295c93abcc9c702b1028c41eafb552c94869483f83c99cb9bebe1bdc58e0d8`|

Полные production/подготовленный скрипты выдаются отдельными файлами, **не хранятся
в Git**. В ветке находятся воспроизводимый генератор
[prepare_schema26_updater.py](../tools/prepare_schema26_updater.py), тесты и этот отчёт.
Генератор принимает только указанный входной SHA, проверяет точность всех anchors,
не исполняет Bash, отказывает при изменённом входе и не перезаписывает source/destination.

```bash
python3 tools/prepare_schema26_updater.py /secure/staging/update_sales.sh \
  /secure/staging/update_sales.schema26.sh
cd /secure/staging
sha256sum -c update_sales.schema26.sh.sha256
bash -n update_sales.schema26.sh
shellcheck update_sales.schema26.sh
```

Исходник должен сохраняться побайтно (UTF-8/LF). Переформатирование редактором или
изменившийся live updater требуют нового аудита, а не обхода SHA-проверки.

## Что делал предоставленный updater915

- All / backend-only / frontend-only, ready images по SALES_UPDATE_BACKEND_IMAGE и
  SALES_UPDATE_FRONTEND_IMAGE, unique release/rollback tags. Build только с явным
  --allow-build, по умолчанию нет pull/build fallback.
- flock9; tracked/index Git clean; только main, fetch/ff-only; отказ при изменении
  защищённых Compose/.env файлов в Git. Image override обязан быть server-only.
- Прежняя цепочка: основной Compose → обычный override → churn-v3u override →
  Calltrack bindings JSON → управляемый report-fix image override. Recovery — последним.
  Содержимое server-only конфигураций не предполагается и не переписывается.
- Один psql coproc держал worker734190221, source734190220, catalog734190117.
  Проверялось владение, а не просто существование процесса. FD-дубликаты/EOF cleanup
  сохранены; обычная работа не переподключает guard молча.
- Node-based patch одного image в YAML; сохранение комментариев/прочих полей;
  hashes эффективного Compose, health+immutable image ID, минимумы диска/RAM,
  приватные checkpoints/logs, signal traps и durable transition marker до DDL.
- Schema job связывал backend DATABASE_URL с уникальной **той же живой** guard-сессией
  через pg_stat_activity/pg_locks. Проверка revision на посторонней БД не засчитывалась.
- Отдельно фиксировался backend commit: ошибка frontend не возвращала healthy backend.

Зависимости от25 находились не только в CLI: schema_python DEFAULT_REVISION, точная
цель migrate/check, допустимый startup image/Compose command, source revision gate,
генерируемый recovery CMD, проверка runtime CMD и классификация исхода ошибки.
Их нельзя исправить массовым25→26: это сломает legacy24→25 и восстановление25.

## Что изменено

### Выбор версии и явная миграция

Цель берётся из **launcher фактического immutable candidate image**, а не из checkout,
который git pull мог обновить до другой версии. Короткий stdlib-only extractor идёт
без сети, read-only, с128МиБ/1CPU/32PID; output и ошибки остаются в приватном backup.
До извлечения уже проверены available RAM/диск. Сохраняются SHA и byte-identical bridge.
После этого schema jobs под effective Compose mounts и старым образом проверяют
тот же SHA. Неподдерживаемый DEFAULT, некорректный launcher, stale command expected25
при candidate26, auto-Alembic/sh entrypoint и недоступный image дают отказ до DDL/up.

| Фактическая БД | DEFAULT candidate | Разрешение | Действие |
|---|---|---|---|
|24|25|--migrate-24-to-25|сохранён прежний отдельный шаг24→25|
|25|25|не требуется|обычный image-only update, без DDL|
|25|26|**--migrate-25-to-26**|новый отдельный шаг25→26|
|26|26|не требуется|обычный image-only update, без DDL|
|26|25|любое|отказ; downgrade не выполняется|
|24|26|любое|отказ; нужны два отдельно одобренных релиза|
|любая|неизвестная|любое|отказ до переключения|

Флаги миграций взаимоисключающие и запрещены с frontend-only. Флаг того же шага
при уже достигнутой целевой версии допустим, но DDL повторно не выполняется.
Обычный startup backend по-прежнему read-only; updater не меняет launcher/бизнес-код.
Migration-job вызывает ровно --migrate/--expected-revision с проверенной целью,
никогда upgrade head, stamp или downgrade. Поддержка26 уже содержится в PR175.

### Блокировки и quiescence

Три прежние locks сохранены в том же порядке; добавлены FTP734190271 и MAIL734190272
из PR175. Все **пять** принадлежат одной psql-сессии, берутся nonblocking и проверяются
перед критическими этапами. Schema job требует все пять в той же БД. При занятости
достаточно одной — безопасный отказ, частично взятые locks освобождаются. Для
cooperating DDL остаётся отдельный SCHEMA_LOCK734190249 внутри launcher.

Старый код25 не знает новых FTP/MAIL locks. Поэтому **только при25→26**, после
подготовки и проверки старого bridge на25 и сохранения/fsync recovery-файлов,
старый backend останавливается до DDL. Проверяются реальный Running=false и прежний
image ID: успешного exit code stop недостаточно. Это короткое согласованное окно
обслуживания, не zero-downtime deploy. Build/import/config/image/preflight ошибки до
этого этапа не пересоздают backend. Уже совпадающая схема и legacy24→25 сохраняют
прежний порядок. БД и frontend этим stop не затрагиваются.

### Recovery

До DDL сохранены исходный YAML, checkpoint, pinned old image, bridge и его SHA,
metadata эффективной конфигурации, staged recovery overlay; durable marker блокирует
повтор после SIGKILL/питания. Это маленькие файлы, **не резервная копия11ГБ PostgreSQL**.
Новая резервная копия БД на тесном VPS автоматически не создаётся: нужна заранее
проверенная внешняя копия оператора.

После ошибки сначала останавливается только собственный surviving migration-job
по уникальному имени и owner label, затем проверяется **фактическая** revision под
guard. Exit code команды не считается доказательством rollback транзакции.

| Исход | Восстановление |
|---|---|
| Ошибка до maintenance stop/DDL | Старый контейнер/YAML остаются; лишнего up нет. |
| Stop/up уже запрошен, но CLI прерван или вернул ошибку | HTTP200 старого процесса может быть временным. Принудительное восстановление только затронутого сервиса, затем image/health/YAML; доDDL остаётся25. |
| Stop состоялся, миграция осталась на25 | Возврат прежнего образа/Compose на25 и health/ID-проверки. |
| Фактическая26, candidate up/check/health/guard не прошли | Старый immutable image **с bridge26**, --rollback/expected26, read-only launcher mount, runtime automation OFF, health/ID/entrypoint/CMD/mount-проверки. Таблицы26 остаются. |
| Backend уже committed, frontend не прошёл | Только frontend rollback; healthy backend26 не откатывается. |
| Guard потерян | Все пять locks повторно берутся **только для восстановления**; при неуспехе unsafe up запрещён. |
| Recovery не подтвердился | Ненулевой exit, критическая ошибка, файлы/образы сохраняются; где возможно YAML согласуется с actual image, но успехом это не объявляется. |
| SIGKILL/power loss | Traps не гарантированы; marker/checkpoints блокируют слепой повтор, необходим ручной аудит actual job/revision/images. |

Сохранены исторические имена `docker-compose.schema25-recovery.override.yml`,
`schema25-recovery.yml`, `/run/sales-schema25-recovery/start_backend.py` и owner-label
`sales.schema25.job`. **Цифра25 в имени не означает версию БД.** CMD/checks/target в них
теперь динамические. Это также сохраняет барьер старого915 updater: он узнаёт
оставшийся recovery file и не обходит его случайно. Переименование файлов не требуется.

Recovery меняет только backend image, entrypoint/CMD, restart/grace, три runtime
analytics flags и один read-only bind. Эффективные остальные services/images,
networks, mounts, mTLS/Calltrack/churn settings сверяются hash-инвариантами.
Нормальный same_settings дополнительно проверяет images **всех нетронутых сервисов**,
а не только hash, игнорирующий images. После начатого переключения восстановление
подтверждает также новый physical container ID: прежний HTTP200 при незавершённом
Docker stop/up не считается доказательством восстановления. Пустой/неизменившийся ID
означает критический неподтверждённый исход, даже если image и HTTP пока совпадают.

Это временный **maintenance rollback**, не возврат полной фоновой работы: lifespan
выключен, FTP/scenario schedulers не стартуют. При этом lifespan OFF **не делает API
read-only** — оператор исключает записывающие запросы и ручные импорты в maintenance
окне. Новые таблицы не удаляются. Даже успешное recovery возвращает исходный ненулевой
код. После maintenance нельзя запускать обычный updater или удалять барьеры вслепую.

## Установка оператором — здесь не выполнялась

1. Объединить175 по обычному review/разрешению и подготовить проверенные готовые
   backend/frontend images релиза853e560/соответствующего merge. Архитектура должна
   совпасть с VPS; сохранить provenance/digests. Скрипт проверяет IDs/launcher, но
   не доказывает происхождение всех файлов образа по одному Git SHA.
2. Подтвердить внешнюю резервную копию, свободный диск/RAM и окно без импорта/
   тяжёлых отчётов/внешних writers. Не менять бюджеты, подписи, policies или настройки
   других проектов. Внешние Calltrack timers не управляются этим updater: их
   согласованное обслуживание остаётся ответственностью оператора.
3. Доставить ready images существующим механизмом. Не собирать их на VPS, не делать
   prune и не удалять rollback images/volumes/импорты. Проверить Docker context:
   команды должны обращаться к Docker именно FirstVDS. Никаких новых remote context
   updater не создаёт. При отсутствии ready image — отказ, не pull/build fallback.
4. Сверить live update_sales.sh с **входным SHA09673f…099d**. При другом файле STOP,
   нужна новая проверка. Поместить prepared.sh/.sha256 в защищённый staging, сверить
   полный SHA из таблицы и выполнить bash -n. Тесты/ShellCheck уже выполнены локально.
5. Сохранить копию рабочего updater в новом уникальном закрытом backup-каталоге
   (не перезаписывать прежние backups). Установить подготовленный файл **рядом**,
   как `/var/www/html/vr/update_sales.schema26.sh`, root:root0700. Рабочий
   `/var/www/html/vr/update_sales.sh` пока оставить без изменений.
6. Проверить, что существующая цепочка overrides/ports/container names совпадает с
   предоставленным скриптом; не упрощать её до одного docker-compose.yml. Не выводить
   полный config/inspect/.env или Calltrack JSON в чат/общий лог.
7. Только после одобрения миграции выполнить следующий **шаблон**, подставив реальные
   локально загруженные immutable IDs. Это не dry-run:

```bash
sudo env \
  SALES_UPDATE_BACKEND_IMAGE='<reviewed backend image ID>' \
  SALES_UPDATE_FRONTEND_IMAGE='<reviewed frontend image ID>' \
  /var/www/html/vr/update_sales.schema26.sh --migrate-25-to-26
```

Backend-only тоже поддержан, но для интерфейса175 требуется новый frontend. Скрипт
сначала проверяет оба выбранных image, затем Git/Compose/guard/preflights. --allow-build
на canonical production пути `/var/www/html/vr/sales` запрещён; legacy opt-in оставлен
только для изолированной разработки. Default minfree остаётся2ГиБ на project/Docker/
backup filesystems, min MemAvailable256МиБ. Если места меньше, обновление останавливается;
не продавливать его снижением порогов без отдельной оценки.

8. Успех требует actual schema26, healthy backend/frontend, равенства image ID
   running container и effective YAML. Источники Git в RUN_ID — **не версия работающих
   контейнеров**. Сверить release.txt/images.txt и сообщения, не публикуя private logs.
9. Проверить защищённую страницу импорта; новый контроль/email по умолчанию OFF.
   Справочник/start_date/SMTP и активация — отдельная административная процедура.
   Ни синхронизация, ни реальные письма/тяжёлые отчёты ради deploy smoke не запускаются.

### Обычное обновление уже26

Те же prepared refs, **без миграционного флага**. Схема должна точно совпасть с DEFAULT
candidate. Все preflights и rollback сохраняются, но migration-job не запускается.
Frontend-only не мигрирует и не перезапускает backend.

### Независимый возврат старого frontend

При healthy backend26, согласованном текущем YAML и отсутствии recovery barriers:

```bash
sudo env SALES_UPDATE_FRONTEND_IMAGE='<saved immutable old frontend ID>' \
  /var/www/html/vr/update_sales.schema26.sh --frontend-only
```

Не использовать backend image25 как ordinary candidate на26 — это будет отклонено.
Для backend нужен описанный bridge recovery, без downgrade PostgreSQL.

### Если установка не завершилась

- Сохранить exit code, backup path, marker/recovery file и образы. Не объявлять
  успехом один HTTP200 старого процесса; важны actual revision и CMD/mount/image.
- При подтверждённом source25 восстановление доDDL проверяется автоматически.
- При26 + MAINTENANCE считать основную причину неустранённой: фоновые функции
  выключены. Исключить пользовательские writes; сохранить обязательный **последний**
  recovery override при любых согласованных restart-командах. Обычный compose up
  без этого файла вернёт несовместимый старый launcher25.
- Если printed CRITICAL или БД/guard недоступны, не запускать повтор и не удалять
  marker/overlay. Нужна адресная проверка named migration-job, actual DB, пяти locks,
  container image/CMD/entrypoint и staged SHA; безопасной универсальной force-команды
  при неизвестном исходе нет. Совместимый launcher и backups уже сохранены.
- Выход из maintenance/удаление только собственного барьера выполняется отдельным
  проверенным планом после устранения причины, возврата prepared candidate26 и
  повторных health/image/schema checks. Деструктивный downgrade/drop новых таблиц
  не используется. Runtime OFF не сбрасывает сохранённые пользовательские настройки;
  перед обычным стартом проверить ранее включённую автоматизацию.

## Проверки и воспроизведение

[Тесты прежних режимов](../backend/tests/test_update_sales_external.py),
[сценарии26 и native PG](../backend/tests/test_updater_schema26.py),
[побайтовая генерация](../backend/tests/test_schema26_preparer.py).
Docker/Git/curl/disk/stop/up в shell-сценариях **подставные**, с canary секретов и
проверкой неизменности рабочего файла. Реальный Bash coproc выполняет последовательные
запросы через дублированные FD после закрытия originals и проверяет EOF/quit.
Real Compose применяется только к config, без up/build. Native payload использует
отдельную localhost PostgreSQL16 БД import_control_test, случайные собственные schemas,
без production DATABASE_URL. Fixtures отказывают при другом host/имени БД.

```bash
SALES_UPDATE_TEST_SOURCE=/secure/staging/update_sales.sh \
SALES_UPDATE_TEST_SCRIPT=/secure/staging/update_sales.schema26.sh \
SCHEMA25_COMPOSE_TESTS=1 \
IMPORT_CONTROL_TEST_DATABASE_URL='postgresql+psycopg://<test>@127.0.0.1:<test-port>/import_control_test' \
PYTHONPATH=backend python -m pytest -q \
  backend/tests/test_schema26_preparer.py \
  backend/tests/test_update_sales_external.py \
  backend/tests/test_updater_schema26.py
```

Опциональные прежние native24→25 проверки требуют отдельного SCHEMA25_TEST_DATABASE_URL;
без него они пропускаются. Источник production updater нужен отдельно, намеренно не
подменяется встроенным макетом. Итог текущей проверки: **101 passed, 2 skipped**
(45 passed/2 skipped — генератор и прежние режимы; 56 passed — схема26, включая
изолированный PostgreSQL16). `bash -n`, полный ShellCheck и `git diff --check` — PASS.

Остаются операционные риски: реальные prepared images/права/внешние timers FirstVDS
здесь недоступны; их служебные проверки выполняются при одобренном запуске. Локальные
тесты доказывают state machine и совместимость launcher/schema, но не прогнозируют
production RSS и не гарантируют восстановление при недоступном Docker/PG или потере
диска. В таких случаях fail-closed сохраняет артефакты, не ослабляя guards/checks.
