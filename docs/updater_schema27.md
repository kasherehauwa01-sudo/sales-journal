# FirstVDS: отдельный prepared-image updater26→27

## Проверенное основание и границы

Релиз приложения: PR177, `fix/import-history-reconciliation`, коммит
`c2eabb69e85d547c25bfcfd2bdbf89497fa035d8`. Production по данным оператора:
`bc457f1`, PostgreSQL `20261109_26`, каталог `/var/www/html/vr/sales`.
Эти сведения не проверялись подключением к FirstVDS. Работающий updater не менялся.

Изучены [историческая сверка](import_history.md), launcher27, миграция27,
`deploy/migrate.import27.explicit.override.yml`,
`deploy/rollback.import27.checked.override.yml`, весь проверенный updater26 и
`tools/prepare_schema26_updater.py` из176. Новых изменений схемы сверх PR177 нет.

| Файл/контракт | Размер/строки | SHA256 |
|---|---|---|
| Входной `update_sales.schema26.sh` |57992 байт /976 LF|`78295c93abcc9c702b1028c41eafb552c94869483f83c99cb9bebe1bdc58e0d8`|
| Новый `update_sales.schema27.sh` |62117 байт /1002 LF|`0b42a3212de64c2aa2b38e394f91309c7ab5cedc7b64aadfb20f350958264c31`|
| Launcher27 из177 |фиксированные проверенные bytes|`62934e47d2377a91238f073cc663d7dc2ea14ec9b404e14993c202548cf411bc`|
| Миграция27 из177 |фиксированные проверенные bytes|`8dc71eacda4f8f7eed74b326e37b87a6efe12f0e47d362ac2bb27dfab3946bd3`|

Полные операторские shell-файлы выдаются отдельно, **не добавляются в Git**.
В PR находятся отдельный `tools/prepare_schema27_updater.py`, тесты и эта инструкция.
Генератор26 не изменён. Новый генератор не запускает shell/Docker/Git, не контактирует
с сервером, принимает только указанный SHA входа, отказывает при неизвестных anchors
и не перезаписывает source/destination/checksum. Изменившийся live updater или
launcher/миграция требуют повторного аудита, а не обхода hash-check.

## Отличия схем и запусков

Миграция27 добавляет две пустые metadata-таблицы `import_history_job` и
`import_history_results`, PK/FK/CHECK/индексы. В исходных sales/sale_items,
import_batches, локальном каталоге и настройках DML не выполняется.
Обычный launcher27 требует actual27 и проверяет frozen физический контракт.
Старый launcher26 после DDL27 **несовместим**; один возврат image26 недостаточен.
Launcher27 также умеет read-only проверить старую26 и служить bridge для old image
на27. Его migration sources допускают26/27, но updater намеренно строже:

| Режим / фактическая БД | Действие |
|---|---|
| Backend или all;26 + `--migrate-26-to-27` |Явный переход26→27, после preflights/stop|
| Backend или all;26 без флага |Отказ до остановки/DDL/up|
| Backend или all;27 без флага |Обычное обновление проверенными image27, без DDL|
|27 + миграционный флаг|Отказ: подтвердить завершение/барьеры, затем обычный режим|
|25,24,неизвестная/ветвящаяся версия|Отказ; цепочечных переходов/downgrade нет|
|Frontend-only без флага|Только image frontend, без остановки/DDL/up backend|
|Frontend-only с флагом;старые миграционные флаги;`--allow-build`|Отказ|

Default — **backend и frontend**. `--backend-only`/`--frontend-only` сохранены.
Первый релиз177 рекомендуется устанавливать default all с явным флагом. Frontend-only
предназначен прежде всего для отдельного обновления/возврата frontend при здоровом
backend; схема guard-БД всё равно должна быть26 или27.

Новый launcher извлекается из **фактического immutable candidate image**, а не
из git checkout. Извлечение без сети, read-only,128МиБ/1CPU/32PID; AST проверяет
DEFAULT/SUPPORTED/MIGRATION_SOURCES и точный SHA всего файла. Тот же SHA проверяется
в каждом schema-job и в старом image с bind-mounted bridge. В candidate дополнительно
проверяются импорты/locks, точный SHA миграции, единственная Alembic head27 и down26.
Перед DDL эти проверки миграции повторяются в самом job. Смонтированная другая
версия launcher, старый `--expected-revision26`, legacy Alembic entrypoint или другая
миграция дают отказ. Полную provenance всех файлов образа не заменяет один SHA
launcher: происхождение/digest/архитектуру prepared images подтверждает оператор.

Standalone override из177 содержит собственный DATABASE_URL для schema-migrate.
Этот updater использует one-off **backend** из полной effective Compose-цепочки,
чтобы не потерять реальный server-only DATABASE_URL, mounts и сеть. SQL подтверждает,
что backend подключён к той же БД с той же живой guard-сессией. Совпадения номера
версии в посторонней БД недостаточно. Recovery использует такой же проверенный
контракт bridge, но собственный entrypoint override устраняет зависимость от CMD
старого image и сохраняет прежние barriers.

## Сохранённые и новые защиты

- Тот же `flock` `/run/lock/update-sales.lock`: старый и новый updater взаимно
  исключаются. Тот же общий marker `.sales-update-recovery-required`.
- Сессия psql/coproc удерживает **шесть** nonblocking advisory locks:
  worker734190221 → source734190220 → catalog734190117 → FTP734190271 →
  SMTP734190272 → CONTROL734190270. Последний нужен для исторической сверки27,
  обычных day checks и изменения справочника. Владение всеми шестью проверяется
  непосредственно в guard PID и schema-job через nonce/current_database.
  Обычная потеря соединения прекращает обновление; новое соединение допустимо
  только для recovery с повторным получением всех locks. Частичное владение
  освобождается. Существующий SCHEMA_LOCK734190249 остаётся внутри launcher.
- FD-дубликаты Bash coproc/закрытие originals/EOF сохранены. Проверены настоящим
  дочерним Bash и несколькими последовательными запросами, не только fake Docker.
- Сохраняется **полный прежний порядок**: основной Compose → обычный override →
  churn-v3u → Calltrack bindings JSON → report-fix image override. Recovery последним.
  Реальные server-only файлы не переписываются; quiet config и fingerprints
  проверяют все настройки и image refs нетронутых services. Управляемый YAML
  меняет только image; исходные bytes/comments/mode сохраняются в checkpoint.
- Только заранее загруженные образы. Нет build-флага, build-ветки или pull fallback.
  Оба выбранных образа разрешаются до Git/DDL. Каждый закрепляется под уникальным
  release tag, прежний — под rollback tag; сверяются actual image IDs.
- У `compose run` нет `--no-build`. Поэтому **только для одноразовых imports/schema
  jobs** добавляется закрытый `run-prebuilt-only.yml` с `build: !reset null` у backend.
  Даже пропавший image не приводит к сборке. Активная Compose-конфигурация не меняется.
  `up` всегда с `--no-deps --no-build --pull never --force-recreate`.
- Чистый tracked/index Git, только main, fetch/ff-only и отказ при изменениях
  deployment-файлов в Git. После pull требуется ancestor c2eabb6 (PR177 сначала
  должен быть объединён). Исходники в RUN_ID не выдаются за версию контейнеров.
- Минимумы остались256МиБ MemAvailable и2ГиБ свободного места на project/Docker/
  backup filesystems. Новые job/DDL не запускаются при недостатке ресурсов.
  Лимиты RAM/swap/cache/timeouts Nginx не меняются; Docker build/prune запрещены.
- ДоDDL fsync маленьких checkpoint/bridge/digest/overlay/marker, не всей11ГБ базы.
  Это **не копия PostgreSQL**: требуется проверенная актуальная внешняя копия.

Перед26→27 после всех staging/preflights старый backend **останавливается**, затем
проверяются Running=false и прежний image ID. Успешного exit code stop недостаточно.
DB/frontend не останавливаются. Locks защищают участвующие в протоколе процессы;
stop исключает фоновые процессы старого web container во время DDL. Другие host-only
Calltrack/systemd сценарии, их внешние HTTP-запросы и произвольные manual writers
updater не может остановить этими locks. Оператор заранее согласует окно обслуживания
и исключает их по существующей процедуре, без изменения подписей/бюджетов/политик.

## История, FTP и email

ДоDDL, послеDDL и после запуска candidate, до commit, считаются **только приватные
хеши** существующих ftp_config/smtp_config/import_control_config (до1000 строк на
таблицу, обычно1). Значения/пароли/получатели не печатаются. Изменение digest — отказ
и проверка; updater не восстанавливает настройки слепыми UPDATE.
Существующие true/false настройки остаются такими, какими были. В частности,
ранее включённый ежедневный FTP/email не отключается и не включается заново.
Никаких новых SMTP-писем, FTP-проходов или исторической сверки ради smoke не вызывается.

На первой26→27 требуется пустота **обеих** новых таблиц послеDDL/активации. Это
подтверждает отсутствие автоматически созданного исторического задания/результатов.
На обычном обновлении27 уже существующая paused/completed история сохраняется,
но running-задание даёт отказ до переключения: администратор сначала ставит его на
паузу штатным API. После релиза история остаётся без запуска до явного действия
администратора. Когда guard отпущен, уже существующие ежедневные механизмы работают
с прежними параметрами; это не новый сигнал включения рассылки.

## Установка оператором — здесь не выполнялась

1. Review/merge PR177 и PR updater, подготовка immutable backend/frontend images
   проверенного релиза вне FirstVDS, подтверждение architecture/digests/provenance.
   Новый updater хранится **рядом** с26, не заменяет его.
2. Подтвердить внешнюю резервную копию actual26/данных, доступное место/RAM и
   согласованное окно без ручных загрузок/внешних writers. Сохранить IDs старых
   образов, chain/config backups и прежние барьеры. Не создавать11ГБ dump на тесном VPS.
3. Доставить ready images существующим способом. Не делать build/pull fallback,
   prune, удаление rollback images, volumes, каталога импортов или чужих данных.
   После доставки должно оставаться не меньше установленного порога; не снижать
   его ради обхода ошибки без отдельной оценки. Проверить, что Docker context —
   именно локальный FirstVDS, а не случайный remote endpoint.
4. Сверить фактический `/var/www/html/vr/update_sales.schema26.sh` с input SHA из
   таблицы. Если он отличается — **STOP**, нужен актуальный файл и новый аудит.
   Не преобразовывать UTF-8/LF в CRLF/BOM редактором Windows.
5. В локальной доверенной checkout подготовить новый файл:

   ```bash
   python3 tools/prepare_schema27_updater.py /secure/staging/update_sales.schema26.sh \
     /secure/staging/update_sales.schema27.sh
   cd /secure/staging
   sha256sum -c update_sales.schema27.sh.sha256
   bash -n update_sales.schema27.sh
   shellcheck update_sales.schema27.sh
   ```

   Генерацию можно выполнить вне VPS. Полный готовый файл/checksum также выдаются
   оператору отдельно. Нельзя перезаписывать source или существующие backups.
6. Скопировать prepared файл/checksum в закрытый staging на FirstVDS, проверить
   sha256sum, установить **новым именем** `update_sales.schema27.sh`, root:root0700,
   рядом с рабочим26. Не заменять `update_sales.sh`/`update_sales.schema26.sh` и
   не делать chmod/chown/пересоздание серверных Compose overrides.
7. Проверить все реальные Compose-файлы и порядок, actual revision26, здоровье
   старых backend/frontend, отсутствие recovery markers/override. Не удалять
   найденные барьеры. Проверить rollback image/bridge27 **доDDL** на изолированном
   стенде с конкретными образами оператора; здесь эти production images недоступны.
8. Только после отдельного одобрения установки/миграции выполнить **шаблон**:

   ```bash
   sudo env \
     SALES_UPDATE_BACKEND_IMAGE='<reviewed local immutable backend image ID>' \
     SALES_UPDATE_FRONTEND_IMAGE='<reviewed local immutable frontend image ID>' \
     /var/www/html/vr/update_sales.schema27.sh --migrate-26-to-27
   ```

   Это реальное обновление, **не dry-run**. Скрипт обновляет Git main; запускает
   только перечисленные выбранные сервисы и отдельный одноразовый job, без зависимостей.
   Нельзя использовать `--migrate-25-to-26`, обычный restart на26 или upgrade head/stamp.
9. Успех требует schema27, frozen preflight, HTTP200 `/api/health` и `/vr/sales/`,
   совпадения effective YAML/running immutable image IDs, стабильных protected hashes
   и отсутствия нового history job/results. Проверить protected UI и приватные
   release.txt/images.txt. Не публиковать `.env`, полный config/inspect/Calltrack JSON
   или raw закрытые logs в чат.
10. Preview шаблонов/исправление/первую историческую сверку выполнять **отдельно**,
    по инструкции import_history.md и только после подтверждения администратора.

Уже27: готовые refs и обычный вызов **без миграционного флага**. Marker/recovery
по-прежнему проверяются. Запуск с миграционным флагом на27 не является командой
восстановления после сбоя и будет отклонён.

## Откат и прерывание

| Подтверждённый исход | Поведение |
|---|---|
| Ошибка staging/config/image/импортов до stop |Старый контейнер не пересоздаётся, YAML остаётся исходным|
| Stop/up запрошен, но CLI прерван/ошибся |Принудительно устанавливается известный контейнер только затронутого сервиса; старый HTTP200 не доказывает завершение RPC|
| DDL не committed, actual26 |Исходный YAML и pinned старый image с его native26; health/image и свежий физический container ID|
| Actual27, новый backend не подтверждён |Pinned старый image + проверенный readonly bridge27, --rollback/expected27, lifespanOFF, runtime analytics flagsOFF; health/image/CMD/entrypoint/mount/invariants|
| Backend committed, ошибка frontend/SIGINT/SIGTERM |Только frontend rollback; рабочий backend27 остаётся|
| Guard утрачен |Новая сессия со всеми6 locks **только для recovery**; при отказе unsafe up не выполняется|
| Rollback не подтверждён/Docker илиPG недоступны |Ненулевой exit/CRITICAL, backups/images/marker остаются; успех не объявляется|
| SIGKILL/питание |Traps не гарантированы; durable marker блокирует повтор, нужен ручной аудит actual job/schema/runtime|

После catchable failure updater сначала останавливает только **свой** migration-job
по уникальному имени и owner label, затем проверяет actual revision. Exit code
Alembic не доказывает rollback транзакции. Нет автоматического downgrade/drop новых
таблиц, восстановления11ГБ backup поверх действующей БД или исправления settings.

Исторические имена `docker-compose.schema25-recovery.override.yml`,
`schema25-recovery.yml`, `/run/sales-schema25-recovery/start_backend.py`,
`sales.schema25.job` намеренно сохранены: старый26 updater узнаёт barrier и не
пытается случайно вернуть несовместимый launcher26. **Цель в CMD — именно27**,
цифра25 в filename не является номером actual schema. Восстановление оставляет этот
обязательный последний override активным; простой compose up без него запрещён.

Maintenance rollback **не возвращает полный обычный режим**: lifespanOFF выключает
background schedulers, но не делает HTTP API read-only. Оператор исключает manual
writers в окне обслуживания. Даже успешное автоматическое recovery возвращает
исходный ненулевой exit code. Сохранённые пользовательские enablement/policies не
сбрасываются. Повтор обычного updater заблокирован до отдельного проверенного плана
выхода из maintenance. Не удалять marker/recovery override ради повторного запуска.

При CRITICAL/SIGKILL сохранить exit code/backup path, проверить surviving named job
и его label, actual version, все6 guards, SHA staged launcher, image IDs и
runtime command/readonly mount/effective chain. Не доверять одному HTTP200 и не
убивать неизвестные контейнеры по шаблону. Нет универсальной безопасной force-команды
при неизвестном исходе. Ручной code rollback возможен только в отдельной процедуре,
удерживающей тот же flock/6-lock session и сохраняющей full chain; подготовленный
backup/schema25-recovery.yml содержит точный pinned old image и bridge27. Активацию
неподтверждённых артефактов без аудита этот updater не автоматизирует.

При здоровом backend27 и **без** recovery barriers старый frontend можно вернуть
отдельно, без backend/DDL:

```bash
sudo env SALES_UPDATE_FRONTEND_IMAGE='<saved old immutable frontend image ID>' \
  /var/www/html/vr/update_sales.schema27.sh --frontend-only
```

## Тестирование и оставшиеся риски

`backend/tests/test_updater_schema27.py` проверяет генератор, source/sidecar
no-overwrite, неверные версии/флаги, подготовленные images/no-build, точную chain,
quiesce/DDL order, orphan job, actual state rollback, сигналы, SIGKILL barriers,
повторный запуск, frontend independence, noop Docker и empty container ID,
реальный Bash coproc/FD/EOF, реальные Compose build-reset/recovery invariants и
native PostgreSQL26→27 с шестью locks/bridge/idle history/protected metadata.

Все shell release-сценарии используют **подставные Docker/Git/curl/df**, собственные
временные каталоги и canary секретов. Настоящий Compose вызывается только config,
без build/up. Native PG ограничен localhost/import_control_test и случайной schema;
рабочие URLs/данные не используются.

Итог: **73 passed,0 skipped** в едином прогоне (316.67секунды), включая native PG16,
реальный coproc и Compose config. `bash -n`, полный ShellCheck, checksum и
`git diff --check` — PASS. Старый генератор26 отдельно повторно проверен без изменений;
результат и входной SHA совпадают с проверенным updater26. Frontend/backend application
code и существующие бизнес-тесты в этом PR не меняются; полный suite приложения
ради изменения offline updater повторно не запускался.

Не проверены live FirstVDS overrides, конкретные prepared images/архитектура,
внешние host timers и production RSS. Script отказывает при drift несовместимой
конфигурации вместо её перезаписи. Контракт migration/launcher строго pinned;
изменившийся177 требует нового review. На тесном диске update может обоснованно
остановиться, даже с готовыми образами. Потеря Docker/PG/диска/питания требует
оператора, а не обещания автоматического восстановления в любых условиях.
Production не посещался; миграции/обновление/письма/историческая сверка там не запускались.
