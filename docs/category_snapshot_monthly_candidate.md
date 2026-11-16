# Кандидат: однократный JOIN и помесячные facts (draft PR189)

## Статус и границы доказательств

Кандидат изменяет построитель снимка; это ещё не разрешение на установку.
FirstVDS не посещался, fill/миграции/recovery/расписание там не запускались.
Новых миграций, постоянных индексов и зависимостей приложения нет.

**Сообщено оператором**, не повторено агентом: на коммите35418d9 в Sales29Lab
1 190 000 продаж/4 600 000 позиций baseline с лабораторным лимитом180сек
занял64,50/108,41сек, no_nested_loop55,76/69,49сек (обратный порядок второго
повтора), EXCEPT ALL0/0. При60сек baseline57014, no_nested_loop47,11сек.
JSON `/home/vasiliy/sales29-compare-180.snO1Tqzv/no-nested-loop-4m6-180s.json`
здесь недоступен. Эти результаты подтверждают необходимость запаса на отдельный
SQL, но не оправдывают отключение Nested Loop или повышение runtime timeout.

**Выполнено агентом**: синтетические100k и4,6млн на отдельном native
PostgreSQL16.2 (pgserver0.1.4, только /tmp, localhost), без рабочего дампа.
Docker здесь не смог создать/запустить контейнер из-за заполненного хранилища.
Это не Docker-прогон Sales29Lab16.16. Среда имеет cgroup8GiB, /tmp на памяти;
она не воспроизводит RAM1,9GiB, swap и диск FirstVDS. Последние прогоны и тесты
на UTF8/C.utf8. Первоначальный C-locale кластер дал сбой Cyrillic lower в одном
тесте; неизменённый35418d9 дал тот же сбой. Создан отдельный Unicode-кластер,
нормализация приложения не менялась.

## Проверенные и отклонённые варианты

Сначала проверены непересекающиеся `[start,next_month)` прямо в исходном SQL.
JOIN, coalesce(code/article), lower(trim(client)), фильтры и cutoff сохранены.
Фактический план зависит от статистики: без свежего ANALYZE датовый индекс
ограничивал sales, но вариант был медленнее; со свежими клиентскими статистиками
sale_items снова Seq Scan каждый месяц. Такой вариант не принят.

Материализация один раз исключает повтор исходных JOIN. Просто добавить индекс
к несортированному входу недостаточно: на4,6млн Bitmap Heap Scan за первый месяц
прочитал49 467 heap blocks (130 226строк), почти весь heap. Этот вариант также
отклонён. Отсутствие `repeated_full_scan_relations` само по себе не доказательство
экономного чтения: проверены actual heap/local buffers, Index Cond и Loops.

Выбран кандидат: один TEMP CTAS с **ORDER BY sale_date**, затем индекс sale_date,
ANALYZE и последовательные месячные INSERT/GROUP BY. На4,6млн первый месяц:
Index Scan, Actual Rows130226, Loops1, Local Read1567/Hit4; диапазон в Index Cond.
Группы содержат year/month, поэтому разбиение не делит одну итоговую группу.
Вся история до прежнего cutoff, включая частичный начальный месяц, сохраняется.

Дополнительно ANALYZE нового клиентского словаря перед facts: автoанализ не видит
его незакоммиченные строки. На модели с реальными FK100k отдельное сравнение
baseline8,030→ANALYZE1,643сек, EXCEPT0/0. Это другой профиль, чем прежний опыт
оператора без такого FK-профиля0,975→1,505: универсального ускорения не заявляем.
Статистика общая для таблицы и может влиять на планы других запросов; нужна
проверка владельца/прав ANALYZE, без автоматической выдачи привилегий.

## Фактические замеры

В финальных прогонах source FK sale_items→sales валидирован; target — обычные
logged таблицы из реальных ORM моделей с PK/FK/check/index. Синтетические источники
TEMP с PK/date/code/article/client indexes; это не копия физического production.
25k/100k/20kпар/2kклиентов и1,19млн/4,6млн/73 412пар/20kклиентов.
Одна пара на вариант, baseline первым; это не статистически устойчивый benchmark.

| Профиль | Baseline facts, с | Кандидат facts, с | Самый долгий месячный INSERT, с | Самый долгий SQL кандидата, с | EXCEPT ALL |
| --- | ---: | ---: | ---: | ---: | --- |
| 100k, прямые месяцы без ANALYZE клиентов | 6,764 | 12,117 | 0,229 | 0,229 | 0/0 |
| 100k, прямые месяцы со статистикой | 1,476 | 7,931 | 0,126 | 0,126 | 0/0; повторный Seq Scan items |
| 100k, материализация без порядка | 1,585 | 3,721 | см. JSON | 0,551 | 0/0 |
| 100k, **упорядоченный кандидат** | 1,505 | 3,258 | 0,030 | 0,577 | 0/0 |
| 4,6млн, **упорядоченный кандидат** | 56,638 | 55,148 | 0,655 | 31,405 CTAS | 0/0 |

Baseline SQL лимит180сек только в лаборатории; каждый SQL кандидата60сек.
work_mem4MB, hash_mem_multiplier1, parallel0, temp_file_limit1GB, RR одинаковы.
Nested Loop включён у обоих. На4,6млн36пакетов; индекс1,358сек, ANALYZE входа0,216сек.
Полное время **этапа facts**, включая CTAS/index/ANALYZE/месяцы —55,148сек.
Вся попытка benchmark63,899сек включает ещё setup/EXCEPT; это не время полного
боевого снимка. Catalog/Clients API, prepare_pairs, coverage и публикация на4,6млн
не замерены вместе: `whole_snapshot_build_measured=false`. Источники здесь уже, чем
production по ширине: sales heap81 199 104, items342 196 224байта против БД14ГБ.

**Общее ускорение не доказано**:55,15 против56,64сек одной пары — близкое время,
100k медленнее. Подтверждено сокращение максимального SQL до31,4сек в этом стенде,
без гарантии60сек на FirstVDS; CTAS остаётся одним запросом по всей истории.

### Диск, temp и WAL на4,6млн

| Измерение | Baseline | Упорядоченный кандидат |
| --- | ---: | ---: |
| Temp Read / Written Blocks корня facts INSERT | 92 025 / 92 143 | 37 317 / 98 555 (сумма36 INSERT) |
| Temp Read / Written Blocks CTAS | — | 100 226 / 100 352 |
| Всего для инструментированных facts SQL | 92 025 / 92 143 | 137 543 / 198 907 |
| Executor WAL facts INSERT, байт | 294 419 083 | 294 412 567 |
| Cluster WAL LSN delta попытки, байт | 302 951 856 | 304 264 336 |
| Target pg_total_relation_size, байт | 116 523 008 | 116 563 968 |
| TEMP вход с индексом, байт | — | **454 131 712 (~433MiB)** |
| TEMP эталон EXCEPT (только benchmark), байт | 104 169 472 | 104 169 472 |

Блок8192байта. Это **суммарный I/O, не пиковый размер temp-файлов**. Корневые
включающие счётчики не складываются с дочерними. CREATE INDEX/ANALYZE, подготовка
и EXCEPT не инструментированы EXPLAIN и не включены в этот temp итог; raw plans
содержат Actual Rows/Loops, external sort Disk, hash batches, buffers. Суммарный
temp I/O вырос; это цена дробления и сортировки. Relation sizes — отдельные
снимки после SQL, не пик общего диска. Cluster LSN включает DDL/metadata/FPI
и любые записи всего кластера; это не эксклюзивный WAL одного INSERT.
WAL на другом сервере зависит от checkpoint/full_page_writes и ширины строк.

1GB temp_file_limit установлен **только в лаборатории**. Он ограничивает
executor temp, не явные TEMP relation433MiB и не WAL. Runtime настройка роли
не меняется; перед установкой отдельно утвердить имеющийся бюджет SQL temp,
TEMP relations, нового/старого снимка и WAL. При53400/57014 не повышать лимиты
автоматически. Неудачные постоянные INSERT могут оставить dead tuples до
штатной уборки; metadata-диагностика теперь показывает n_dead_tup. Не добавлены
автоматические VACUUM/повторы/удаление поколений.

Артефакты: [summary](benchmarks/category_snapshot_monthly/summary.json), рядом
`.json.gz` с исходными полными планами и SHA256 распакованного JSON в summary.
Старый `materialized-stats-physical-4m6` — контроль без source FK, не финальный
профиль; включён для проверки отклонённого broad bitmap плана. Распаковка:
`gzip -dc FILE.json.gz > /tmp/report.json`. В файлах только синтетические данные.

## Транзакции, ошибки и тесты

`category_snapshot_facts.py` вызывается внутри прежней REPEATABLE READ транзакции.
Нет промежуточных commit, смены isolation или planner override. Прежние advisory
locks/защита от импорта/headroom/атомарная публикация остаются. TEMP ON COMMIT DROP
исчезает и при rollback. Runtime REPORT_SETTINGS/SQL60сек неизменны. Месячные
границы не зависят от даты первой покупки/возраста товара и не обрезают историю.

**48 passed,1 deselected,109,62сек**: snapshot plan lab, monthly, build diagnostics,
category seasonality unit/PostgreSQL; исключён старый test_snapshot_benchmark.
Включены timeout и CancelledError во втором месяце после реально вставленного
первого, первая/повторная ручная и плановая попытки: частичный снимок не публикуется,
старый active сохраняется, первая next_due=NULL, последующий ручной запуск успешен.
Прежняя политика следующего планового слота сохранена. Сравнение полного builder
старым SQL и кандидатом через EXCEPT ALL: NULL/пустые поля/кириллица/ведущие нули,
отрицательные/дробные количества/NULL цены/leapday/cutoff; все суммы совпали.
Полный backend-suite и Docker/production интеграция не запускались.

## Повтор на Sales29Lab (не FirstVDS)

Только уже локальные `postgres:16-alpine`, `sales-updater29-test-deps:local`.
Запас10GiB — admission для стенда, не расчёт пика и не production требование.
Если Docker VM storage не виден из WSL, остановиться и отдельно проверить его
в VM, не подменять свободным местом WSL. Не использовать production compose/env.

```bash
: "${LAB_BASE:?Set a writable large-disk directory on Sales29Lab}"
set +x
set -euo pipefail
umask 077
test -f backend/benchmarks/benchmark_snapshot_facts.py
PG_IMAGE=postgres:16-alpine
PY_IMAGE=sales-updater29-test-deps:local
docker image inspect "$PG_IMAGE" "$PY_IMAGE" --format '{{.Id}}'
DOCKER_ROOT=$(docker info --format '{{.DockerRootDir}}')
test -d "$DOCKER_ROOT" || { echo 'STOP: verify Docker VM storage separately'; exit 1; }
for path in "$DOCKER_ROOT" "$LAB_BASE"; do
    df -Pk "$path"
    test "$(df -Pk "$path" | awk 'NR==2 {print $4}')" -ge 10485760
done
awk '/MemAvailable:/{print}' /proc/meminfo
# PG max1536MiB + runner512MiB + OS; do not proceed without actual headroom.
LAB_ROOT=$(mktemp -d "$LAB_BASE/sales29-monthly.XXXXXXXX")
PG_NAME="sales29-monthly-${LAB_ROOT##*.}"
mkdir -p "$LAB_ROOT/pgdata" "$LAB_ROOT/results"
LAB_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
printf 'POSTGRES_USER=lab\nPOSTGRES_DB=category_snapshot_lab\nPOSTGRES_PASSWORD=%s\nPOSTGRES_INITDB_ARGS=--encoding=UTF8\n' "$LAB_PASSWORD" > "$LAB_ROOT/postgres.private.env"
printf 'LAB_DATABASE_URL=postgresql+psycopg://lab:%s@127.0.0.1:5432/category_snapshot_lab\nDATABASE_URL=postgresql+asyncpg://lab:%s@127.0.0.1:5432/category_snapshot_lab\n' "$LAB_PASSWORD" "$LAB_PASSWORD" > "$LAB_ROOT/client.private.env"
unset LAB_PASSWORD
# No --locale=C: use image Unicode locale; inspect lc_ctype before tests.
docker run -d --pull never --name "$PG_NAME" --network none --memory 1536m \
    --env-file "$LAB_ROOT/postgres.private.env" \
    -v "$LAB_ROOT/pgdata:/var/lib/postgresql/data" "$PG_IMAGE"
for attempt in $(seq 1 30); do
    if docker exec "$PG_NAME" pg_isready -U lab -d category_snapshot_lab >/dev/null 2>&1; then break; fi
    sleep 1
done
docker exec "$PG_NAME" pg_isready -U lab -d category_snapshot_lab
docker exec "$PG_NAME" psql -U lab -d category_snapshot_lab -Atc 'SHOW server_version; SHOW lc_ctype; SHOW lc_collate;'
docker exec "$PG_NAME" sh -c 'df -Pk /var/lib/postgresql/data'
run_lab() {
    docker run --rm --pull never --network "container:$PG_NAME" \
        --memory 512m --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp:rw,size=32m \
        --env HOME=/tmp --env-file "$LAB_ROOT/client.private.env" \
        --env PYTHONDONTWRITEBYTECODE=1 --env PYTHONPATH=/app \
        -v "$PWD/backend:/app:ro" -v "$LAB_ROOT/results:/out:rw" -w /app \
        "$PY_IMAGE" python -m benchmarks.benchmark_snapshot_facts "$@"
}
set +e
run_lab --confirm-synthetic-lab --sales 25000 --items 100000 \
    --products 10000 --clients 2000 --repetitions 1 --comparison monthly_materialized \
    --physical-target --fresh-client-statistics --baseline-timeout-seconds 180 \
    --sql-timeout-seconds 60 --output /out/monthly-100k.json \
    > "$LAB_ROOT/results/100k.private.log" 2>&1
small_rc=$?
set -e
printf '100k exit=%s; inspect %s\n' "$small_rc" "$LAB_ROOT/results/monthly-100k.json"
```

После проверки100k/свободного диска/RAM и статуса complete/EXCEPT0/0 выполнить
следующий блок вручную в той же shell. Не засчитывать его до появления JSON.

```bash
df -Pk "$DOCKER_ROOT" "$LAB_ROOT"
awk '/MemAvailable:/{print}' /proc/meminfo
set +e
run_lab --confirm-synthetic-lab --sales 1190000 --items 4600000 \
    --products 36706 --clients 20000 --repetitions 1 --comparison monthly_materialized \
    --physical-target --fresh-client-statistics --baseline-timeout-seconds 180 \
    --sql-timeout-seconds 60 --output /out/monthly-4m6.json \
    > "$LAB_ROOT/results/4m6.private.log" 2>&1
large_rc=$?
set -e
printf '4m6 exit=%s; inspect %s\n' "$large_rc" "$LAB_ROOT/results/monthly-4m6.json"
python3 - "$LAB_ROOT/results/monthly-4m6.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]));print(r['status'],r['input'])
for x in r['runs']:
 print(x['variant'],x['status'],x['phase'],x['sqlstate'],
       x.get('elapsed_seconds'),x.get('longest_measured_sql_seconds'),
       x.get('comparison'),x.get('storage_final'))
PY
```

Сохранить full estimated/actual plans, staging/batches/spills, source_fk_validated,
effective settings, cluster WAL и размеры. Проверить bounded range Index Scan
input, actual heap/local read, не только отсутствие Seq Scan. Если baseline
не завершён, equivalent=NULL, сравнение не засчитывать. Если кандидат57014/53400,
оставить draft, не повышать60сек автоматически. Для обратного порядка отдельно
запустить тот же100k/4,6млн с repetitions2 и новым output (не перезаписывать).
После сохранения результатов остановить **только свой** `docker stop "$PG_NAME"`.
Каталог PG/результаты и образы оставить; prune/down-v/удаления не нужны.

## Следующая попытка FirstVDS — только после ревью

1. Повторить Docker-стенд выше с проверенными образами; оценить планы и запас
   диска с учётом реальной ширины/старых снимков/мёртвых строк/WAL. При ограничении
   RAM/диска не устанавливать кандидат только по данным native-стенда.
2. Утвердить привилегии ANALYZE и локальный бюджет temp отдельно; этот PR не
   меняет глобальную конфигурацию. Сохранить резервную копию и предыдущий image29.
3. Собрать готовый backend-образ вне VPS, обновить обычным проверенным updater
   после разрешения оператора; схема29 сохраняется, миграций нет. Frontend не менялся.
4. Перед fill оператору повторно выполнить READ ONLY metadata-диагностику из
   исходного runbook, проверить schema29, RAM/диск, отсутствие импорта/worker,
   failed-попытку/active_id/next_due. Не сбрасывать attempts или настройки.
5. Только после этих проверок один ручной `python -m app.category_seasonality fill --confirm`.
   Не параллельно с импортом, без автоматических повторов и без ослабления locks.
6. При успехе проверить ready snapshot/active_id, totals/coverage, stage durations,
   сохранённый cutoff и next_due. Расписание включается только прежней успешной
   публикацией; этот PR не включает его при установке/первом отказе.
7. При ошибке сохранить stage/SQLSTATE/метаданные без SQL-параметров; убедиться,
   что старый active не изменился, первый next_due осталсяNULL. Не форсировать
   публикацию и не запускать retry до анализа. Откатить прежний image29 штатным
   updater при необходимости; не делать downgrade/drop/stamp или очистку продаж.
