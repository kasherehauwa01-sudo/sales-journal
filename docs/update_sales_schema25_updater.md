# Реальный update_sales.sh: явная миграция24 →25 и безопасный code rollback

## Проверенный входной файл и выдаваемый артефакт

Оператор предоставил полный `update_sales.production.sh`, скопированный с VPS.
Прочитан целиком именно этот файл, не прежний 499-строчный update_sales.new.sh.

| Файл | Байт | Строк LF | SHA256 |
|---|---:|---:|---|
| Полученный production-скрипт | 29027 | 512 | `3ecaf732d26a2f7c59bfdfb199c9408c647943ff8554297807494b88ab427f2c` |
| Исправленный полный `update_sales.schema25.sh` | 49477 | 872 | `5fb64f092b4dd01291068410e2e6c37ea71530e6ac00ffdcfde791a4b14b7d60` |

Полные файлы исходного и исправленного updater **не хранятся в Git и не включены
в PR** по требованию оператора. Исправленный файл и `.sha256` выдаются отдельно в
чате Codex. Их следует сохранять как UTF-8/LF без BOM и без преобразования строк.
Этот документ содержит аудит и порядок работы, а не копию production-скрипта.

Production не посещался, рабочий серверный файл не заменялся, production migration
не выполнялась. Наличие такого же файла на VPS проверяет оператор перед установкой.

## Вывод аудита512

Исходный скрипт хорошо разделяет image-only переключение и подготовку: flock,
проверка tracked Git, один PSQL coproc с тремя locks, временный build override,
уникальные release/rollback tags, image ID + HTTP health, checkpoint YAML, обработка
сигналов и сохранение приватных резервных файлов.

Но его rollback восстанавливает прежний YAML/image и не меняет старый launcher24.
После внешней миграции25 это может вернуть образ, который не проходит startup.
Кроме того, rollback включается лишь после изменения YAML/начала up. Если DDL уже
закоммитилась, но ошибка возникла раньше переключения, прежний работающий процесс
может оставаться healthy на25, хотя после restart его launcher24 уже не запустится.

Нельзя считать HTTP200 старого процесса доказательством готовности старого образа
к следующему запуску. Нельзя считать ошибку команды миграции доказательством,
что БД осталась24. Для signals внутри logged-function статусные сообщения также
могли уходить в закрытый лог вместе с перенаправлением команды.

## Новый процесс

Сохранены первоначальные default all, backend-only и frontend-only (также явный
legacy аргумент `all`). Новый флаг **`--migrate-24-to-25`** — единственное разрешение
этого updater на DDL. С frontend-only он запрещён. Без флага на24 будет отказ;
автоматического upgrade head или stamp нет. На25 повторная миграция не запускается.

1. Проверка файлов/Git/disk/available RAM, flock и исходного healthy backend/image.
2. Те же три session locks: worker734190221, source734190220, catalog734190117,
   в прежнем порядке и одной PSQL-сессии. Нормальная работа не переподключается
   молча после потери guard; переподключение допускается только для восстановления.
3. Исходная Compose-цепочка сохраняется: docker-compose.yml → обычный override →
   churn-v3u → Calltrack bindings JSON → report-fix image override. Восстановление
   build остаётся только во временном последнем override, как в512. mTLS/другие
   mounts/environment/networks не переписываются.
4. Candidate собирается отдельно либо берётся готовый локальный image через
   `SALES_UPDATE_BACKEND_IMAGE`. Старый immutable ID закрепляется уникальным tag.
5. Проверяется actual candidate startup: неизвестный image entrypoint/workdir,
   legacy `alembic upgrade head`, прямой uvicorn и stale command overrides отвергаются
   **до DDL**. Разрешён проверяемый python -B start_backend.py на /app с default25
   либо явной expected revision25. Неизвестный entrypoint старого образа также
   требует отдельного аудита; updater его не обходит наугад.
6. Совместимый launcher копируется в закрытый backup, chmod0644 только для bind
   mount файла в UID10001. Сверяется SHA **внутри candidate и старого one-off образа**.
   Старый образ заранее проходит read-only preflight на текущей схеме, до миграции.
7. Backend schema job доказывает через pg_stat_activity/pg_locks, что его DATABASE_URL
   видит ту же уникально именованную guard-сессию с тремя locks. Совпавшей revision
   на другой БД недостаточно. Status берёт shared SCHEMA_LOCK249, не читает24 сквозь
   незавершённую cooperating migration. Работа выполняется готовым образом, с тремя analytics
   flags OFF/SHADOW, переопределённым Python entrypoint, без бизнес-schedulers.
8. Перед DDL fsync только маленьких checkpoint/launcher/recovery файлов и их каталогов,
   затем атомарное эксклюзивное создание/fsync `.sales-update-recovery-required`.
   Это не flush всей11-ГБ БД и не новый dump. Marker защищает от повторного запуска
   после SIGKILL/отключения питания, когда Bash traps уже не работают.
9. Только с явным флагом24 →25 запускается точный native launcher migration-job.
   У job уникальные name/label. При ошибке/сигнале сначала останавливается только
   собственный surviving job, затем выясняется actual revision; не выбирается24,
   пока DDL ещё может выполняться. Не трогаются db/backend/другие контейнеры этим stop.
10. После25 проходят candidate и старый образ с launcher25. Перед переключением
    снова проверяются memory/disk/guard. Затем image-only up, HTTP/image ID/Compose
    checks и прежняя commit boundary.

Обычный успешный запуск не включает recovery override. Source/image/version записи
разделены: Git SHA не выдаётся за версию работающего контейнера. Check-only/schema
служебный Python не печатает credentials или данные клиентов. Полная private Compose
конфигурация проходит через pipe/memory, на диск пишутся hashes/images/booleans.
Raw build/command diagnostics остаются только в защищённом backup0700/logs0600.
Не публиковать содержимое этих приватных логов целиком.

## Исходы ошибок

| Ошибка | Поведение |
|---|---|
| Build/import/startup/старый preflight до DDL | Рабочие YAML/container не переключаются, старый backend не пересоздаётся. |
| Миграция откатилась, actual24 | Проверяются старый healthy backend/image/YAML; без начавшегося switch нет up. Только подтверждённый собственный marker очищается. |
| Команда вернула ошибку, но actual25 | Схема не понижается; активируется отдельный maintenance override старого immutable image с совместимым launcher25. |
| Новый backend up/health/config/guard failure после25 | Тот же rollback25: actual runtime Entrypoint/Cmd/readonly launcher mount/flags и image ID проверяются вместе с health. Старый процесс принудительно пересоздаётся, даже если прежний HTTP ещё200. |
| Потеря guard | Требуется повторно получить все три locks только для rollback; если нельзя — никакого unsafe up, критическая ошибка, checkpoints/marker сохраняются. |
| Rollback не подтвердился | Нет сообщения об успешном восстановлении. Где возможно, согласуется хотя бы image в effective YAML с actual container; runtime/health могут оставаться непроверенными. Marker блокирует повтор. |
| Frontend failure после backend commit | Откатывается только frontend checkpoint; новый healthy backend и25 сохраняются. |
| SIGINT/SIGTERM в logged/migration/up | Обработчик использует сохранённый status FD; сообщения видны оператору. Транзакционный исход определяется, а не предполагается по exit code. |
| SIGKILL/power loss | Traps не гарантированы. Durable marker блокирует следующий запуск; требуется ручная проверка named job, revision, locks и images. |

Recovery добавляет **последним** отдельный
`docker-compose.schema25-recovery.override.yml`: old image tag, явный Python launcher25,
--rollback/expected25, readonly mount файла, restart=no, grace30s и три flags OFF/SHADOW.
Все остальные Compose-параметры должны совпасть с исходным recovery-invariants hash.
Нельзя незаметно изменить Calltrack/mTLS/DB/network/прочие сервисы этим исключением.

Это maintenance recovery: старые lifespan FTP/scenario workers выключены, HTTP требует
проверки выбранного образа. Не выдавать его за полную работу всех фоновых функций.
Даже при подтверждённом восстановлении updater возвращает исходный ненулевой exit code.

Ни successful recovery, ни critical recovery не удаляют резервные файлы/образы/таблицы.
При maintenance остаются last override и marker. Следующий updater отказывает прежде
build/pull/up: нельзя случайно проигнорировать recovery overlay. Не удалять их вслепую
и не запускать прежний512: он не знает этих барьеров.

## Ресурсы VPS

Default остаётся минимум2GiB свободного места на project/Docker/backup filesystem.
Добавлен minimum MemAvailable256MiB, который нельзя опустить ниже этого значения.
Проверки лёгкие (df, /proc/meminfo), выполняются до подготовки, перед DDL и переключением.
Это **не доказательство**, что Docker build поместится в память: concurrent requests
и другие проекты могут изменить available RAM/диск после проверки.

При1,9ГБ RAM, занятом swap и3,1ГБ диска рекомендуемый способ — заранее проверенный
готовый backend image и **--backend-only**. Не собирать frontend без необходимости.
Не запускать тяжёлые API/импорт/сборки параллельно в maintenance window. Никакого
увеличения swap/кешей/таймаутов Nginx, prune или новых полных backups на VPS.
Уже проверенный backup на компьютере оператора сохраняется; отдельно учесть его
актуальность и восстановимость. Никаких11-ГБ копий этой миграции не требуется.

Готовый image должен соответствовать фактическому main после разрешённого merge PR171.
Updater сверяет launcher bytes, но **это не заменяет** проверку происхождения всего
image/архитектуры/immutable ID оператором. Ничего не pull-ится автоматически для
готового image. Если он отсутствует локально, операция останавливается.

## Точная ручная установка файла (не выполнялась Codex)

1. Получить отдельные `update_sales.schema25.sh` и `.sha256` из чата. Проверить SHA,
   размер49477,872 LF строк и `bash -n` на локальной Linux-машине/WSL.
2. После отдельного разрешения скопировать файл и checksum во временный каталог VPS,
   **не в рабочий путь и не в Git checkout**. Ничего не запускать при копировании.
3. На VPS проверить actual SHA существующего `/var/www/html/vr/update_sales.sh`.
   Если он отличается от `3ecaf732…27f2c`, STOP: данная версия не считается аудированной.
   Проверить рабочий скрипт на обычный файл, не symlink, и отсутствие другого updater.
4. Пример будущих операторских команд, только после разрешения:

```bash
cd /tmp/sales-updater-reviewed
sha256sum -c update_sales.schema25.sh.sha256
bash -n update_sales.schema25.sh
python3 -c 'import yaml'
sha256sum /var/www/html/vr/update_sales.sh

# Не заменять работающий скрипт без совпадения original SHA и проверки выше.
install -d -m 0700 /var/backups/sales-journal/scripts
SALES_UPDATER_BACKUP="/var/backups/sales-journal/scripts/update_sales.$(date -u +%Y%m%dT%H%M%SZ).sh"
cp -a -- /var/www/html/vr/update_sales.sh "$SALES_UPDATER_BACKUP"
install -o root -g root -m 0750 update_sales.schema25.sh /var/www/html/vr/update_sales.sh.reviewed
bash -n /var/www/html/vr/update_sales.sh.reviewed
mv -f -- /var/www/html/vr/update_sales.sh.reviewed /var/www/html/vr/update_sales.sh
```

Эти команды **только устанавливают файл**, не выполняют обновление. Если исходный
owner/group отличаются от root:root, согласовать права отдельно; не менять их наугад.
Не запускать shell с `-x`, не source скрипт и не копировать файл через редактор с CRLF/BOM.

## Точный рекомендуемый запуск24 →25 (отдельное разрешение)

Сначала отдельно разрешённый merge PR171, проверенные candidate+old images и репетиция
на изолированном стенде. Предыдущая [инструкция launcher25](churn_schema25_deployment.md)
описывает физический контракт и standalone jobs; **не мигрировать отдельно работающий
launcher24 до подготовки отката**. Рекомендуется этот единый guarded процесс:

1. Проверить backup, main/clean Git, текущую24, actual old image ID, SHA рабочего
   updater, дисковый/RAM запас и конфигурацию всех пяти server Compose-файлов.
2. Подготовить совместимый image **вне VPS**, загрузить отдельно разрешённым способом,
   проверить весь образ и его ID; не держать ненужную tar-копию рядом с распакованными
   слоями при нехватке места. Не удалять прежний working image.
3. Остановить/дождаться импортов и тяжёлых расчётов штатным механизмом; внешние старый
   и новый churn timer/consumer оставить приостановленными. Не изменять approved
   policies, identities, signatures или pilot budgets и не включать Calltrack.
4. Только после разрешения выполнить:

```bash
# REVIEWED_IMAGE_ID — проверенный локальный sha256 ID, не секрет и не mutable latest.
SALES_UPDATE_BACKEND_IMAGE=REVIEWED_IMAGE_ID \
  /var/www/html/vr/update_sales.sh --backend-only --migrate-24-to-25
```

Не выполнять предварительно ручной Alembic upgrade25, обходя marker/preflight этого
процесса. При build/compatibility error до DDL файл не получает права «попробовать
миграцию всё равно». На уже25 для обычного update флаг migration не нужен.

5. При успехе проверить effective Compose image ↔ actual container `.Image`,
   `http://127.0.0.1:8010/api/health`, actual revision25, три новые таблицы и disabled
   permanent control. Проверить полезные HTTP read APIs без тяжёлых расчётов.
   Backend commit очищает только свой transition marker. Backups/old tags сохраняются.
   Frontend не менялся; отдельной причиной для его rebuild этот PR не является.

## Если активировался recovery

Записать выданный backup path, не удалять marker/last override/launcher/old tags.
Все будущие операционные Compose-команды обязаны включать тот же исходный порядок
пяти файлов и recovery последним, иначе YAML-модель будет отличаться от runtime.
Не выполнять compose up/pull/build через старые shortcuts/updater512.

Исходная цепочка из512, при отсутствии operator env overrides:

```bash
SJ_COMPOSE=(docker compose --env-file /var/www/html/vr/sales/.env
  -f /var/www/html/vr/sales/docker-compose.yml
  -f /var/www/html/vr/sales/docker-compose.override.yml
  -f /var/www/html/vr/sales/docker-compose.churn-v3u.override.yml
  -f /etc/calltrack/churn-identity/sales-bindings.compose.json
  -f /var/www/html/vr/sales/docker-compose.report-fix.override.yml
  -f /var/www/html/vr/sales/docker-compose.schema25-recovery.override.yml)
"${SJ_COMPOSE[@]}" config --quiet
```

Учитывать фактические SALES_PROJECT_DIR/SALES_CALLTRACK_COMPOSE, если они применялись.
Не печатать полный config или Env. Проверить health/actual image/runtime и revision
read-only. При critical recovery даже image alignment может быть недоступен: это
**не подтверждённый rollback**, ручная диагностика обязательна. Файлы оставить.

Выход из maintenance — отдельный план: исправить candidate, проверить его на25,
согласовать final effective config/image и flags OFF, выполнить разрешённое
переключение и проверки, затем отдельно разрешить снятие барьеров. Простое удаление
overlay может восстановить прежние true flags, поэтому **не удалять его и marker
как способ «починить обновление»**. Calltrack automation/timers не возобновляются
в рамках этого технического исправления. Не запускать downgrade/stamp/drop/restore
11-ГБ БД ради code rollback и не возвращать к исполнению updater512 на схеме25.

## Воспроизводимые проверки

`backend/tests/test_update_sales_external.py` получает путь через
`SALES_UPDATE_TEST_SCRIPT`; сам production код в tests/fixtures не копируется.
Docker/Git/HTTP/disk обновления полностью подставные, пути только во временном
проекте, контрольный working script остаётся нетронутым; другие сервисы проверяются hashes/config-тестом.

Покрыты build/import/startup/old image failures, отсутствие consent,24 rollback,
commit25 при ошибке команды, orphaned migration stop, новый backend up/health,
runtime mismatch при healthy HTTP, lost/busy guard, frontend после commit,
prebuilt без build, RAM/disk, persistent recovery/marker, SIGINT/SIGTERM/SIGKILL.
Реальный Bash coproc проверяет несколько запросов после закрытия исходных FD и EOF.
Реальный Compose config проверяет recovery normalization без запуска контейнеров.
На локальном PostgreSQL16.15 проверяется служебный payload: одна настоящая тройная
guard-сессия, read-only status, отдельная явная тестовая24 →25, отказ для чужого nonce.
Это только синтетическая БД localhost/analytics_shadow_test, не production migration.

Прогоны — на отдельной dev/Linux-машине, не VPS. Без внешнего файла эти tests явно
SKIP: это требование не хранить скрипт в Git, а не утверждение о выполненных проверках.
Пример для мока без native PostgreSQL:

```bash
cd backend
SALES_UPDATE_TEST_SCRIPT=/absolute/path/update_sales.schema25.sh \
  PYTHONPATH=. python -m pytest -q tests/test_update_sales_external.py
```

Для native тестов требуется специально созданный изолированный PG16 и guarded
SCHEMA25_TEST_DATABASE_URL; production DATABASE_URL как test source не используется.
SCHEMA25_COMPOSE_TESTS=1 включает только локальную проверку config.
Окончательный изолированный прогон: **1046 passed, 46 skipped**, включая
44 проверки внешнего updater; одна предупреждающая запись AnyIO. `bash -n` и
ShellCheck 0.11.0 проходят. Native проверки выполнены на PostgreSQL 16.15.
Пропущенные тесты не считаются выполненными.

Полный updater **не запускался с реальными Docker build/up/Git pull**, фактический
старый production image и все бизнес-API на нём в Codex недоступны. До операции
обязательна отдельная репетиция выбранных реальных images/overrides в изоляции.
Preflight скрипта — необходимая защита, а не доказательство production recovery.
