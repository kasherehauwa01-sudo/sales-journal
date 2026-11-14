# Updater29 и recovery29: schema28 → schema29

Рабочие серверные скрипты не заменяются этой разработкой. Ниже — действия
оператора в согласованное окно, не команды, уже выполненные на FirstVDS.
База разработки: main `607b1de9ffe120130655f4584b68672aa6ceef27` (PR186).
Изменений расчётов, PostgreSQL volumes и бизнес-данных в генераторах нет.

## 1. Подготовка вне production

Новые генераторы используют прежнюю цепочку26→27→28, а затем отдельные точные
замены контрактов28→29. Предыдущие генераторы и shell recovery entry не изменены.
Входом остаётся **проверенная копия updater26**, не произвольный updater28:

| Артефакт | SHA256 |
| --- | --- |
| Проверенный исходный updater26 | `78295c93abcc9c702b1028c41eafb552c94869483f83c99cb9bebe1bdc58e0d8` |
| Воспроизведённый updater28 | `3450adae185c5d51a0cf3fff1956389a7edfa0055f0b4e349f6b4ee4402ec9c2` |
| Воспроизведённый recovery28 | `5ea6a71f65af29324fb53a1a110a5d4bc7e25745339364fce8973632c4383692` |
| Launcher29 | `8a5dfc4bd27a0e3a618fa5390f4b88c6e610f3ea7e9e93be4368e77823d31935` |
| Миграция29 | `c6b388b04a574123ea1a669485d429d2afc51125ab301c3df284f3b260af00e4` |
| Итоговый updater29 | `32530332a3fdb0972bf70be20d44d69c863e361a5400cbaabfafc3fa12c89f1d` |
| Итоговый recovery29 | `1166a5fb6cf93f440ac75e1599c6bf372f37b4e1348e25f79f7cdc7e6987ebf8` |

Python3 с PyYAML, Bash и ShellCheck нужны на машине подготовки. Задать свои пути:

```bash
REVIEWED26=/absolute/path/to/reviewed/update_sales.schema26.sh
OUT=/absolute/path/to/new/empty/release29-directory
mkdir -m 700 "$OUT"
sha256sum "$REVIEWED26"
python3 tools/prepare_schema28_updater.py "$REVIEWED26" "$OUT/update_sales.schema28.sh"
python3 tools/prepare_schema28_recovery.py "$REVIEWED26" "$OUT/recover_sales.schema28.sh"
python3 tools/prepare_schema29_updater.py "$REVIEWED26" "$OUT/update_sales.schema29.sh"
python3 tools/prepare_schema29_recovery.py "$REVIEWED26" "$OUT/recover_sales.schema29.sh"
(cd "$OUT" && sha256sum -c update_sales.schema29.sh.sha256 && sha256sum -c recover_sales.schema29.sh.sha256)
bash -n "$OUT/update_sales.schema29.sh"
bash -n "$OUT/recover_sales.schema29.sh"
shellcheck "$OUT/update_sales.schema29.sh" "$OUT/recover_sales.schema29.sh"
```

Генераторы отказываются перезаписывать вход, выход или существующий sidecar,
при другом входном SHA и изменении любой из двух прежних воспроизводимых версий28.
Shell-файлы содержат приватные серверные контракты; в Git включены только
генераторы, тесты и инструкция. Не коммитить исходный серверный updater.

### Обязательная сверка реального сервера оператором

Не считать локальный результат копией действующего FirstVDS updater. Оператор
задаёт `OPERATOR_UPDATER28` фактическим путём и выполняет только чтение:

```bash
sha256sum -- "$OPERATOR_UPDATER28"
```

Ожидается SHA updater28 из таблицы. При несовпадении **остановиться**: получить
актуальный файл, проверить отличия и подготовить отдельную reviewed адаптацию.
Не менять SHA в allowlist для обхода отказа. Аналогично проверить используемый
recovery28, если он установлен. Этот PR не подтверждает содержимое FirstVDS.

Проверенный исходник задаёт порядок Compose:

1. `--env-file "$SALES_PROJECT_DIR/.env"`;
2. `docker-compose.yml`;
3. `docker-compose.override.yml`;
4. `docker-compose.churn-v3u.override.yml`;
5. файл `SALES_CALLTRACK_COMPOSE`;
6. `docker-compose.report-fix.override.yml`;
7. recovery override последним, только в режиме восстановления.

Оператор должен сверить **весь** действующий порядок, server-only mounts,
Calltrack/churn/report-fix/mTLS, UID/права и имена сервисов. Исходный контракт
использует контейнеры `sales-journal-backend`, `sales-journal-frontend`,
`sales-journal-db` и localhost8010/8011. Это данные проверенного исходника,
а не независимое подтверждение текущего сервера. Другой состав overrides,
команда запуска или пути требуют пересмотра; не добавлять их наугад.
Не публиковать `compose config`, `docker inspect Env` или `.env`: возможны секреты.
Генераторы сохраняют приватные результаты Compose в закрытой контрольной точке.

Подготовить проверенную внешнюю резервную копию. Никаких автоматических
pg_dump11ГБ, очистки Docker или удаления rollback-образов скрипты не выполняют.
Сохранить исходные YAML, серверные файлы, предыдущие полные image IDs и сами
образы; их доступность не заменяет резервную копию базы.

Новые backend/frontend собрать и проверить на отдельной машине. Загрузить
доверенным согласованным способом **до** окна обновления. Updater не build/pull;
принимает только полный `sha256:` плюс64 lowercase hex, проверяет локальный ID.
Теги допустимы лишь как дополнительные уникальные checkpoint-метки, не вход релиза.
Launcher и миграция пинятся байт-в-байт; другой будущий launcher требует review.
Проверить предыдущий фактический production image с bridge29 на изолированной
копии БД29 до окна обслуживания: он в данной среде недоступен.

## 2. Установка и переключение

Установить оба артефакта **отдельными новыми файлами**, проверить SHA/права.
Не перезаписывать updater28, рабочий update_sales.sh или recovery-файлы.
Переменные ниже заполняет оператор; их нельзя заменять предположениями агента:

```bash
export SALES_PROJECT_DIR=/operator/verified/project
export SALES_CALLTRACK_COMPOSE=/operator/verified/calltrack-compose
export SALES_UPDATE_LOCK_FILE=/operator/verified/shared-flock
export SALES_UPDATE_BACKUP_ROOT=/operator/verified/private-backup-directory
export SALES_UPDATE_BACKEND_IMAGE='sha256:FULL_VERIFIED_NEW_BACKEND_ID'
export SALES_UPDATE_FRONTEND_IMAGE='sha256:FULL_VERIFIED_NEW_FRONTEND_ID'
UPDATER29=/operator/installed/update_sales.schema29.sh
RECOVERY29=/operator/installed/recover_sales.schema29.sh
```

Значения `FULL_...` — намеренно неисполняемые placeholders, нужны реальные64hex.
Общий flock должен быть **тем же**, что у предыдущих updaters. Не выбирать новый
путь блокировки для обхода занятого lock. Не менять MEMINFO_FILE на production.
Пороги доступной RAM256МиБ и свободного места2ГиБ сохранены и не могут быть
снижены параметрами. Это минимумы admission, не гарантия достаточности для БД/WAL.
При меньшем запасе остановиться и согласовать ресурсы; не удалять резервные образы.

После утверждения окна и фактической схемы28:

```bash
bash "$UPDATER29" --migrate-28-to-29
```

Сначала проверяются чистота tracked Git, источник28/цель29, режим, Compose,
образы/launcher/граф и SHA миграции, возможность bridge, RAM/диск и отсутствие
незавершённого recovery. Git fetch/fast-forward разрешены только этой процедурой,
исходники релиза должны включать указанный merge607b1de. Противоречивые режимы и
источники27/другие ревизии отклоняются до изменения приложения.

Guard удерживает **одной PostgreSQL-сессией** шесть advisory locks:
734190221,734190220,734190117,734190271,734190272,734190270.
Проверяется владение каждым lock; отдельная schema lock launcher сохранена.
Занятая/потерянная guard-сессия, действующая историческая сверка или строящийся
category snapshot дают отказ. Coproc имеет отдельные дублированные FD; закрытие
исходных каналов не мешает последовательным assert_guard.

До DDL создаются закрытые checkpoint/marker и закрепляются прежние образы,
затем останавливается старый backend. Одноразовый контейнер нового backend
с исходной полной конфигурацией явно мигрирует28→29. Проверяются фактическая
alembic_version, физические таблицы/FK/CHECK/index через launcher29 и пустое
неактивное состояние снимков. Обычный запуск контейнера не мигрирует базу.

FTP/SMTP/import_control_config/**реальные** sales_control_settings хешируются
до/после/после активации. Оба конца перехода включают sales_control_settings:
в отличие от28-перехода их существующие значения не подменяются defaults.
`SALES_PROTECTED_V1` требует единственную строгую JSON-запись с guard nonce,
действием, ревизией и SHA, успешный код процесса. Служебные строки Compose
допускаются; голый64hex, иной контекст, дубликаты/повреждения/пустота запрещены.
Реальный drift останавливает обновление, настройки автоматически не исправляются.

После DDL переключается backend: HTTP, immutable runtime ID, alignment Compose,
обычный launcher/entrypoint, PID1 uvicorn, отсутствие bridge и lifespan-off,
физическая схема, настройки и guard. Затем отдельно frontend и `/vr/sales/`.
Независимый сбой frontend возвращает **только frontend**. При успешном обновлении
сохраняются checkpoint и образы, rollback traps отключаются.

На уже подтверждённой29 обычные команды:

```bash
bash "$UPDATER29"                 # backend, затем frontend, без DDL
bash "$UPDATER29" --backend-only
bash "$UPDATER29" --frontend-only
```

Миграционный флаг на29 запрещён. Frontend-only на28 запрещён. Обычный backend
switch29 тоже предварительно сохраняет marker, чтобы его сбой нельзя было
обойти повторным запуском. При marker требуется recovery, не удаление защиты.

## 3. Ошибки и recovery29

| Фактическое состояние | Действие |
| --- | --- |
| Сборка/pull нужны либо candidate preflight не прошёл | Отказ, работающий backend не пересоздаётся; исправить артефакты вне VPS |
| До DDL, доказана28 | Штатное восстановление предыдущих образов/исходного YAML |
| Миграционный процесс вернул ошибку | Проверяется фактическая ревизия; код возврата не доказывает rollback DDL |
| Уже29, новый backend не подтверждён | Старый закреплённый код + проверенный RO launcher29, maintenance/lifespan OFF |
| Backend29 подтверждён, frontend сломан | Вернуть frontend; backend29 оставить, восстановление frontend отдельно |
| SIGINT/SIGTERM/потеря PostgreSQL | Безопасный rollback только при доказанном состоянии и locks; иначе marker/checkpoint остаются |
| SIGKILL/неоднозначное `switching` | Отказ от слепого повтора; ручной аудит фактических контейнеров/схемы/контрольных точек |

После29 старый launcher28 **не запускается напрямую**. Bridge монтирует только
launcher29 read-only в прежний image; `--rollback --expected-revision 20261114_29`
проверяет замороженный физический контракт29 и импортирует прежний `/app`.
Lifespan OFF отключает фоновые задачи. Не монтировать весь новый backend поверх
старого. Не downgrade/stamp и не удалять новые таблицы/снимки/volumes.

Преднамеренно сохранены общие legacy-имена `.sales-update-recovery-required`,
`docker-compose.schema25-recovery.override.yml`, mount
`/run/sales-schema25-recovery/start_backend.py` и job-label. Они препятствуют
обходу барьера старым updater. Это не разрешение ревизии25. Новое durable
состояние выхода хранится в `exit29-state.json` и `exit29.*` в checkpoint.

Recovery29 запускается **только на уже committed29**, без миграционных действий.
Из исходной checkpoint и утверждённой записи оператора взять старые полные IDs
и **исходный** protected-before.digest. Не подставлять текущий digest только
ради обхода расхождения. Recovery проверит их против доверенных артефактов.

```bash
CHECKPOINT=/operator/verified/private/update.checkpoint
EXPECTED_PROTECTED_SHA=OPERATOR_VERIFIED_ORIGINAL_64HEX
OLD_BACKEND_ID='sha256:FULL_VERIFIED_OLD_BACKEND_ID'
OLD_FRONTEND_ID='sha256:FULL_VERIFIED_OLD_FRONTEND_ID'
bash "$RECOVERY29" \
  --checkpoint "$CHECKPOINT" \
  --protected-sha256 "$EXPECTED_PROTECTED_SHA" \
  --old-backend-id "$OLD_BACKEND_ID" \
  --old-frontend-id "$OLD_FRONTEND_ID"
```

Новые IDs и пути берутся из тех же SALES_* переменных. Проверяются права/владелец,
symlinks, SHA артефактов, схема29, полная Compose-цепочка, исходный bridge и образы,
настройки/активные задания и те же locks/flock. Recovery сохраняет neutral staging,
переключает backend, затем frontend. Marker снимается **последним** после всех
проверок normal runtime и настроек; recovery override архивируется после проверки
эффективного Compose. При ошибке backend восстанавливается проверенный bridge.
При ошибке frontend стадия `backend_committed` сохраняется; повтор **той же**
команды восстанавливает frontend без повторного перезапуска backend.

Неудачный автоматический rollback оставляет marker, YAML и резервные данные для
аудита. Не делать ручной compose up, rm marker/override или повтор миграции.
При изменённых артефактах/неоднозначной стадии сначала проверить состояние
оператором и согласовать отдельный план; recovery не должен угадывать его.

## 4. Первое заполнение — отдельное разрешение

Updater, recovery, обычный restart и открытие отчёта не запускают fill и не
включают недельную задачу. После миграции active_id/next_due NULL, снимков нет.
Сначала закончить обновление, проверить релиз/health/защищённые настройки и
освободить updater locks. Оператор отдельно оценивает реальную кардинальность,
несколько поколений снимков, строящийся снимок, WAL, SQL temp и нагрузку соседних
сервисов. Локальный размер тестовых снимков не прогнозирует production.

Только после отдельного разрешения, в новом проверенном backend с той же
конфигурацией, выполнить **внутри контейнера**:

```bash
python -m app.category_seasonality fill --confirm
```

Внешнюю команду запуска оператор строит по проверенной Compose-цепочке, не по
выдуманному имени контейнера. Расчёт не совмещать с импортом/другими тяжёлыми
операциями; headroom/locks не отключать, агрессивные retry loops не использовать.
После первого успешного fill ожидается следующая суббота19:00 по Москве.
Проверить опубликованный снимок/last_success/next_due и выборочные итоги отчёта.
FTP, SMTP, получателей и утверждённые политики эта процедура не меняет.

## 5. Проверки и границы доказательств

- 71 локальный тест updater/recovery, генераторов и протокола: подставные
  Docker/Git/Compose проверяют переходы, отказы, ошибки и сигналы до/после commit,
  независимый frontend rollback, checkpoint/resume, потерю guard и конфигурации.
  Включены backend-only/frontend-only на29, no-overwrite обеих CLI, ошибочный
  процесс с правильной записью и реальные Bash coproc/дочерний процесс:
  последовательные запросы после закрытия исходных FD и корректные quit/EOF.
- На изолированном PostgreSQL16 прошли2 native-теста: реальные DDL28→29,
  guard6locks/потеря lock/nonce, сохранение включённой настройки уведомлений,
  повторная миграция запрещена; старый **код** f8b00e6 с read-only launcher29
  действительно стартовал через uvicorn, `/api/health`200 и lifespan OFF.
  До заполнения active_id/next_due NULL и0снимков.
- Ещё15 профильных регрессионных тестов отчёта/launcher29 прошли на изолированном
  PostgreSQL16.15; нагрузочный benchmark исключён.
- `bash -n` и ShellCheck0.11.0: оба итоговых артефакта без ошибок.
- Воспроизводимость updater28/recovery28 и пины29 проверяются тестом и генератором.
- Настоящий local Docker Compose тест добавлен, но прогон остановлен **до
  миграции** ошибкой Docker `no space left on device` при подготовке тестовой БД.
  Он **не пройден**. Защиты места не снижались, образы/данные не удалялись.
- Фактический предыдущий production image и текущие FirstVDS overrides недоступны.
  Native-запуск старого кода и mocks не доказывают работу конкретного старого
  production image. До установки нужен успешный Compose-прогон на отдельной
  машине с запасом диска, затем проверка фактического rollback image/overrides.

### Повторение тестов вне production

```bash
export SALES_SCHEMA29_SOURCE="$REVIEWED26"
export SALES_UPDATE_TEST_SCRIPT="$OUT/update_sales.schema29.sh"
PYTHONPATH=backend pytest -q backend/tests/test_updater_schema29.py backend/tests/test_schema29_recovery.py
```

Native-тестам нужен изолированный PG16, роль с правом CREATE SCHEMA и URL
`IMPORT_CONTROL_TEST_DATABASE_URL` с localhost и **именем import_control_test**.
Fixture создаёт/удаляет только свой случайный synthetic schema. Нельзя указывать
production или копию, доступную рабочему приложению. Не публиковать пароль URL.
Без URL native-тесты пропускаются; это не успешная проверка PostgreSQL.

Compose-тест opt-in: только выделенный локальный Docker socket, свободные
8010/8011 и отсутствие контейнеров с проверенными именами. Требует отдельного
тестового dependency image `sales-updater29-test-deps:local` на Python3.12 с
backend/requirements.txt и console-командой uvicorn. Например, собрать такой
слой по Dockerfile ниже **на машине тестирования**, не на FirstVDS:

```dockerfile
FROM python:3.12-slim-bookworm
WORKDIR /deps
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
```

Тест сам локально собирает код old/new поверх этого слоя; frontend — HTTP stub,
не UI-тест React. Нужны Git-объекты f8b00e6 и607b1de и зависимости PyYAML/pytest.
Сборку слоя выполнить отдельно с `-t sales-updater29-test-deps:local`, затем:

```bash
SALES_SCHEMA29_COMPOSE=1 PYTHONPATH=backend pytest -q backend/tests/test_schema29_compose.py
```

Тест не выполняет fill, работает только со своими новыми контейнерами/синтетикой,
оставляет собственные images/checkpoints/PG bind-data и останавливает свои сервисы.
Данные хранятся вне автоматического pytest cleanup в `/tmp/sales-updater29-compose-*`.
При неудаче не повторять поверх оставшихся имён: проверить и сохранить результаты.
Нельзя освобождать место удалением rollback-образов/чужих volumes. Успех этого теста
всё равно не заменяет проверку конкретного production image и server-only mounts.
