# Schema27: исправление проверки настроек и выход из bridge recovery

## Основание и подтверждённые границы

Аудит выполнен на исходниках `2cb379e`. Со слов оператора FirstVDS уже имеет
`20261111_27`, healthy старый image26 с launcher27 `--rollback` (lifespan OFF),
старый frontend26, marker и последний recovery override. Проверенная контрольная
точка: `/var/backups/sales-journal/updates/update.1TDLjiMH`.
Эти факты **не проверялись удалённым подключением**. Действия ниже выполняет оператор
в согласованное окно. Никаких операций на FirstVDS при разработке не выполнялось.

Ошибка установлена в `prepare_schema27_updater.py`: два `cmp -s` сравнивали
полные `protected-before/after/activated.log`, включая предупреждения и сообщения
Compose. Это могло вернуть ошибку после успешно committed миграции при одинаковых
настройках. Миграция повторно не требуется.

Новая проверка получает ровно один `SALES_PROTECTED_V1` JSON record с точными полями
`protocol`, `guard`, `action`, `revision`, `sha256`. Проверяются версия протокола,
nonce живой шестиблокировочной сессии, конкретный этап и схема, нижний hex64 digest;
повторные JSON-ключи/неизвестные поля/повреждённые или несколько records запрещены.
Сначала требуется exit0 самого Compose/Python job. Затем сравниваются только
валидированные digest-файлы. Просто строка из64 символов результатом не является.
Шум Compose вне records не влияет на сравнение. Raw logs остаются закрытыми.
Состав защищённых данных не менялся: целиком строки `ftp_config`, `smtp_config`,
`import_control_config`, с прежним лимитом1000 на таблицу; значения не печатаются.
Реальное отличие digest по-прежнему блокирует обновление; слепого UPDATE настроек нет.

## Два разных инструмента

1. `prepare_schema27_updater.py` формирует исправленный **обычный updater** для
   новых26→27 и уже27 без recovery. Он по-прежнему отказывает при marker/override.
2. `prepare_schema27_recovery.py` формирует **отдельный recovery executable**.
   Он требует существующий marker и подтверждённый checkpoint, уже committed27,
   четыре полных immutable image IDs и явно подтверждённый прежний digest настроек.
   Старый bare SHA из legacy-log не выдаётся за новый доверенный protocol record.
   Для этого случая оператор сообщил одинаковый SHA до/после:
   `1b7a64ebc84d2ff5a70948b368a0f6aaf59f63e248d628aed2e35d2a11a27f58`.
   Этот baseline передаётся явным параметром и сверяется новыми read-only jobs.

Оба генератора принимают **только** проверенный updater26:
`78295c93abcc9c702b1028c41eafb552c94869483f83c99cb9bebe1bdc58e0d8`.
Не передавайте им текущий updater27/другой production-скрипт; при drift нужен аудит.
Launcher27 и migration27 остаются pinned прежними SHA, исходники приложения,
миграции, схема/данные, бизнес-политики и лимиты не меняются.

## Что делает recovery executable

- Берёт прежний `flock /run/lock/update-sales.lock` и шесть session advisory locks
  одной psql/coproc-сессией:734190221,734190220,734190117,734190271,734190272,734190270.
  Каждая критическая фаза проверяет владение ими; backend job подтверждает ту же
  сессию/БД по nonce. При потере guard продолжение запрещено; повторное получение
  locks допустимо только для восстановления bridge/текущего frontend.
- Не делает Git fetch/pull, Docker build/pull, Alembic migrate/downgrade/stamp.
  Normal main удалён из generated recovery. Shared frontend transaction запрещает
  backend-аргумент, embedded schema job вообще не имеет DDL action.
- Проверяет exact Alembic27, отсутствие running migration containers по прежней
  owner label, frozen launcher контракт таблиц/FK/CHECK, отсутствие running history
  job, неизменность защищённых настроек. Paused/completed history не удаляется;
  требование пустых history-таблиц здесь не применяется к уже существующей27.
- Проверяет root/operator ownership, regular files, отсутствие небезопасных
  symlink/permissions, структуру marker и его связь с checkpoint. Четыре image IDs
  должны существовать локально и разрешаться в самих себя.
- Сохраняет полный порядок: base → override → churn-v3u → Calltrack bindings →
  report-fix → recovery последним. Настройки/mounts/volumes не сериализуются заново;
  управляемый image scalar меняется прежним точечным YAML editor. Для one-off jobs
  применяется прежний `build: !reset null`; up — no-deps/no-build/pull-never.
- Проверяет исходный healthy bridge26+launcher27 и frontend26, совпадение runtime
  IDs с effective Compose, readonly mount bridge и прежний maintenance delta.
  Сравнивает нормальную цепочку со старым normal checkpoint: снятие bridge не
  должно потерять Calltrack/mTLS/churn или чужие service image refs.
- На immutable backend27 проверяет extraction/SHA launcher, imports, migration
  graph/hash, frozen schema27. Также проверяет старый immutable backend26 с bridge27
  на **нынешней27** до переключения. Проверка не запускает lifespan или задания.
- Перед backend switch fsync сохраняет state `switching`, normal YAML, исходный
  recovery override/marker/bridge. Сначала пишет новый backend image в managed YAML
  (пока bridge всё ещё активен), затем атомарно заменяет recovery содержимым
  `services: {}`. Это нейтрализация delta, не удаление файла. Проверяет effective
  configuration и только затем force-recreate backend.
- Проверяет backend health200, immutable image, согласованность YAML, normal
  entrypoint/CMD, отсутствие bridge mount, прежние automation flags, PID1 uvicorn
  без `--lifespan off`, повторный schema/settings/history check. При таком launcher
  HTTP200 достигается после startup lifespan, который создаёт прежние FTP/scenario
  schedulers. Прямая интроспекция живых asyncio tasks не выполняется; запуск новых
  задач ради smoke не производится. Ранее включённые ежедневные механизмы смогут
  продолжить работу после отпускания locks, новые настройки не включаются.
- Durable `backend_committed` записывается **до frontend**. Затем используется
  прежняя отдельная frontend transaction с rollback image/YAML/health.
- Только после обоих healthy images, schema/settings и равенства effective Compose
  с/без нейтрального recovery последний архивируется рядом (`*.saved`), никогда не
  удаляется без проверки. Marker снимается **последним** и только если он побайтно
  совпадает с сохранённым, включая исходный run_id. Старые backups/images остаются.

State: `exit27-state.json` добавляется в указанный старый checkpoint; прежние файлы
не перезаписываются. Отдельные `exit27.XXXXXXXX` хранят каждый запуск, секретные
логи0700/0600, durable фазы и SHA backup-файлов. Обычный updater остаётся заблокирован
вплоть до полного успеха. Recovery не является dry-run.

## Подготовка вне FirstVDS

Используйте reviewed checkout с этим исправлением и копию **updater26**. Выполнить
на рабочей машине, не на VPS; существующие destinations генератор не перезапишет:

```bash
python3 tools/prepare_schema27_updater.py /secure/staging/update_sales.schema26.sh \
  /secure/staging/update_sales.schema27.fixed.sh
python3 tools/prepare_schema27_recovery.py /secure/staging/update_sales.schema26.sh \
  /secure/staging/recover_sales.schema27.sh
cd /secure/staging
sha256sum -c update_sales.schema27.fixed.sh.sha256
sha256sum -c recover_sales.schema27.sh.sha256
bash -n update_sales.schema27.fixed.sh
bash -n recover_sales.schema27.sh
shellcheck update_sales.schema27.fixed.sh recover_sales.schema27.sh
```

Проверенные готовые артефакты (UTF-8/LF, без секретов конфигурации):

| Файл | Байты / LF-строки | SHA256 |
|---|---|---|
| `update_sales.schema27.fixed.sh` |63883 /1037|`b035ccf5d533d0746ff01e70cdd59bcbd8eff0a9d527792e3c3e98be87f5d6d7`|
| `recover_sales.schema27.sh` |78976 /1239|`6f104052a27563e82d7446f62d48f28eb210f15b8eb5c2249dc074cc7a714d52`|

Передайте новые файлы и checksum на FirstVDS прежним утверждённым способом.
Работающий updater27/26 и server-only конфигурацию не заменять автоматически.
Никакой пересборки backend/frontend для исправления updater не нужно: оба новых
образа, по данным оператора, уже загружены.

## Команды оператора на FirstVDS — здесь НЕ выполнялись

До запуска: подтвердить внешнюю резервную копию, локальный Docker context/endpoint,
окно без manual writers и host-only Calltrack/systemd писателей, свободные≥2ГиБ на
project/Docker/backup filesystems и≥256МиБ MemAvailable. Скрипт не отключает защиту
памяти, не очищает диск и не делает11ГБ dump. Если места меньше — STOP, согласовать
безопасное освобождение/расширение; не удалять rollback images/volumes.

Пример доставки в `$HOME/sales-release27` (каталог staging оператор выбирает сам):

```bash
cd "$HOME/sales-release27"
sha256sum -c update_sales.schema27.fixed.sh.sha256
sha256sum -c recover_sales.schema27.sh.sha256
sudo install -o root -g root -m 0700 update_sales.schema27.fixed.sh \
  /var/www/html/vr/update_sales.schema27.fixed.sh
sudo install -o root -g root -m 0700 recover_sales.schema27.sh \
  /var/www/html/vr/recover_sales.schema27.sh
sudo bash -n /var/www/html/vr/recover_sales.schema27.sh
# Только безопасные поля, без полного inspect/env/config:
sudo docker inspect -f '{{.Image}}' sales-journal-backend
sudo docker inspect -f '{{.Image}}' sales-journal-frontend
sudo awk '/^previous / {print}' \
  /var/backups/sales-journal/updates/update.1TDLjiMH/images.txt
```

Сверьте два текущих **старых** image IDs с `previous` в checkpoint, не подменяйте их
новыми. Не публикуйте raw logs/.env/Compose/Calltrack конфиги. Ниже shell читает IDs
из текущих healthy старых контейнеров: выполнить только после указанной сверки.

```bash
OLD_BACKEND_ID=$(sudo docker inspect -f '{{.Image}}' sales-journal-backend)
OLD_FRONTEND_ID=$(sudo docker inspect -f '{{.Image}}' sales-journal-frontend)
sudo env \
  SALES_UPDATE_BACKEND_IMAGE=sha256:ce98cdc91c77aaae6edc74ffb05ebc4bedd17381b6b9d2cc5c095909b6275ba1 \
  SALES_UPDATE_FRONTEND_IMAGE=sha256:d17aefe016c079791b2ecd4e6cdcbaec1c9b92d606b44c0b7e5fa5846f75de8c \
  /var/www/html/vr/recover_sales.schema27.sh \
  --checkpoint /var/backups/sales-journal/updates/update.1TDLjiMH \
  --protected-sha256 1b7a64ebc84d2ff5a70948b368a0f6aaf59f63e248d628aed2e35d2a11a27f58 \
  --old-backend-id "$OLD_BACKEND_ID" --old-frontend-id "$OLD_FRONTEND_ID"
```

Сохранить обе OLD_* переменные/полные IDs и путь нового checkpoint до закрытия
сессии. **При продолжении после frontend failure нельзя перечитывать OLD_BACKEND_ID
из уже нового контейнера**: использовать изначально сохранённый старый ID.
Команда целиком повторяется только при явно поддерживаемой фазе (см. ниже).
Не передавать `--migrate-26-to-27`: recovery его не принимает.

После сообщения УСПЕХ выполнить безопасные независимые read-only проверки:

```bash
sudo docker exec sales-journal-db sh -c \
 'exec psql -X -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version_num FROM alembic_version"'
sudo docker inspect -f '{{.Image}}' sales-journal-backend
sudo docker inspect -f '{{.Image}}' sales-journal-frontend
curl -fsS http://127.0.0.1:8010/api/health
curl -fsS http://127.0.0.1:8011/vr/sales/ >/dev/null
sudo test ! -e /var/www/html/vr/sales/.sales-update-recovery-required
sudo test ! -e /var/www/html/vr/sales/docker-compose.schema25-recovery.override.yml
```

Не активировать историческую сверку/новую email-рассылку/Calltrack automation ради
проверки. Проверить UI и прежние показатели, штатное расписание — наблюдением.
В дальнейшем обычное обновление27 — исправленным updater **без** migration flag,
с теми же prepared images правилами и всей действующей override chain.

## Ошибка/прерывание: порядок действий

| Этап | Безопасное поведение |
|---|---|
| Refusal до переключения |Исходные bridge/YAML/контейнеры не трогаются, marker остаётся. Исправить причину, не удалять защиты|
| Backend up/health/runtime/settings failure или INT/TERM в switch |Получить6 locks (заново только для rollback), восстановить оба исходных YAML и bridge, force-recreate только backend, подтвердить health/ID/CMD/mount/effective Compose. Схема остаётся27|
| Backend committed, frontend failure/INT/TERM |Вернуть только frontend к началу текущей попытки, сохранить normal backend27. Marker и нейтральный override остаются. Повтор с **теми же четырьмя IDs/baseline/checkpoint** продолжает frontend без backend up|
| Ошибка архивирования/снятия marker |Normal runtime остаётся; marker не удаляется вслепую. Поддерживаемый backend_committed retry заново проверит всё, без backend up|
| PG/Docker недоступны или bridge не восстановился |CRITICAL, без объявления успеха. Сохранить marker/state/все backups. Не выполнять compose up вручную и не снимать barriers|
| SIGKILL/питание при state=switching |Нет автоматического решения по одному HTTP200. Повтор **отказывается**; нужен аудит actual schema/runtime/обоих YAML и последней команды. Фазу руками не менять|

При успешном восстановлении bridge после ошибки код остаётся ненулевым. Фаза
`bridge` допускает повтор тех же аргументов. Каждый повтор создаёт новую staging
папку/теги, старые не перетирает. Изменившийся marker/state/image/config — отказ.
Для неподтверждённых исходов универсальной force-команды здесь нет; оператор
сохраняет закрытые logs, сопоставляет image/CMD/override под тем же flock+6 locks
и готовит отдельный проверенный план. Нельзя делать downgrade/stamp/drop новых
таблиц, удалять marker, удалять volumes или запускать updater26 поверх27.

## Тестирование

Команды профиля из корня checkout (локальная `.venv`, только тестовая localhost БД):

```bash
export SALES_UPDATE_SCHEMA26_SOURCE=/secure/staging/update_sales.schema26.sh
export SALES_UPDATE_TEST_SCRIPT=/secure/staging/update_sales.schema27.fixed.sh
export PYTHONPATH=backend
export PATH="$PWD/.venv/bin:$PATH"
# IMPORT_CONTROL_TEST_DATABASE_URL: отдельная localhost БД import_control_test;
# тесты откажутся работать с другим host/database. Не использовать production URL.
SCHEMA27_COMPOSE_TESTS=1 .venv/bin/pytest -q backend/tests/test_updater_schema27.py
.venv/bin/pytest -q backend/tests/test_schema27_recovery.py
```

Тесты покрывают structured protocol/noise/nonzero exit, настоящее изменение
защищённых данных, missing/duplicate/malformed record, guard/action/schema context,
миграцию26→27 на отдельной PG16 schema, FK drift, bridge, active history refusal,
Bash coproc/FD/EOF, Compose config/build-reset и fake-Docker release transactions.
Recovery отдельно воспроизводит уже committed27 + старый healthy bridge/frontend,
INT/TERM, no-op up, потерю guard, повтор после frontend failure, архивирование,
неверную схему и повреждённые barriers. Реальные production image bytes и live
FirstVDS overrides не доступны здесь: fake Docker не доказывает runtime ваших
конкретных образов. Перед переключением recovery проверяет их на самом сервере.

Ограничения: после SIGKILL/недоступности инфраструктуры автоматическое восстановление
не гарантируется; raw logs закрытые, но резервные файлы могут содержать секреты —
не публиковать. Посторонние writers, не участвующие в locks, исключаются оператором.
Этот PR не исправляет/не меняет продажи, расписание, SMTP и бизнес-политики.

Фактические результаты этого исправления:

- `test_updater_schema27.py`: **77 passed,0 skipped**,446.49с. Включены native PG16
  (после перехода уже schema27, настоящий protected-settings drift и удалённый FK),
  реальные Bash coproc и Docker Compose config.
- `test_schema27_recovery.py`: **27 passed,0 skipped**,328.61с. Полный цикл fake-Docker
  recovery/ошибок/продолжения, no-DDL, сохранность override chain и barriers.
- Отдельно повторены8 сценариев протокола/неуспешного job/frontend resume/config drift:
  **8 passed**; это повторная проверка, а не ещё8 уникальных тестов.
- Оба готовых файла: `bash -n`, ShellCheck, sidecar checksum, детерминированная
  регенерация из неизменённого SHA-pinned updater26 и `git diff --check` — PASS.
- Полный suite приложения не повторялся: app/backend/frontend/migrations в этом PR
  не изменялись. Настоящие backend/frontend контейнеры с production images не
  запускались; PostgreSQL только отдельный localhost `import_control_test`.
