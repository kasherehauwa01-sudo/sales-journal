# Порционная подготовка facts: draft PR189 после b717e81

## Причина следующего изменения

Сообщено оператором Sales29Lab PostgreSQL16.16: прежний monthly_materialized
на4,6млн получил partial. Первый кандидат завершился EXCEPT ALL0/0 с максимальным
SQL59,33сек; второй (обратный порядок)57014 на staging_input. Baseline180сек
завершён в обоих. На100k candidate15,05сек против baseline5,73сек.
Каталог `/home/vasiliy/sales29-monthly.R8Cabi7p/results` здесь недоступен.
Это сведения оператора, не наш повторный прогон. Предыдущий единый CTAS не является
устойчивым исправлением. [Прежние замеры](category_snapshot_monthly_candidate.md)
сохраняются как история, а не подтверждение готовности к production.

## Новый кандидат

1. Создать пустой TEMP-вход из прежних выражений, WHERE false: PostgreSQL сам
   сохраняет типы/точность столбцов; данные не читаются.
2. По sales_pkey получать границу следующих10000 **существующих** sales, без
   OFFSET и без предположения о плотности ID. Заполнить вход через
   `i.sale_id >= :start AND i.sale_id < :end`. Следующая порция начинается с end.
   Разрывы ID не создают миллионы пустых диапазонов. Последняя порция меньше.
3. Исходные JOIN, coalesce(code/article), lower(trim(coalesce(client,''))),
   cutoff и выражения quantity/price сохранены; добавлены только ID-границы.
   Существующий индекс sale_items(sale_id) обязателен для приемлемого плана.
   Постоянный индекс/миграция не добавлены. Каждый товар/строка источника попадает
   только в один диапазон, даже если его продажи есть во многих месяцах.
4. Отдельным SQL создать **упорядоченную по sale_date** TEMP-копию. Нельзя просто
   использовать несортированный вход с индексом: прежний опыт показал чтение
   почти всего heap для одного месяца. Удалить только свой сырой TEMP после
   успешной сортировки. Отдельно построить индекс даты, отдельно ANALYZE.
5. Прежние месячные INSERT/GROUP BY по полуоткрытым датам, без изменения итогов.

Все действия — одна прежняя REPEATABLE READ транзакция; промежуточных commit нет.
Прежние advisory locks, блокировка импорта, headroom перед порциями/сортировкой/
индексом/ANALYZE/месяцами и атомарная публикация сохранены. При ошибке откатываются
оба TEMP-входа и все новые facts; active не заменяется, первая next_due остаётся
NULL. Существующая политика повторного планового слота не меняется.

SQL кандидата **60сек**, глобальные настройки, интерактивные отчёты и Nested Loop
не менялись. ANALYZE клиентского словаря из b717e81 сохранён. Размер порции —
10000 продаж, не жёсткий лимит позиций/байт: при необычно большой продаже или
другом плане запрос всё ещё может получить57014. Автоматических повторов или
повышения лимита нет. Stage facts_input означает порционную подготовку;
facts_sort/facts_index/facts_statistics измеряются и диагностируются отдельно.
Логи содержат только порядковый номер пакета, длительность, stage/SQLSTATE;
не содержат ID продаж, клиентов, параметров или SQL.

## Фактически выполнено здесь

Native PostgreSQL16.2 (pgserver0.1.4) на localhost, UTF8/C.utf8, синтетика без дампа.
Docker здесь по-прежнему недоступен из-за заполненного storage; ничего чужого не
удалялось. /tmp — память, cgroup8GiB; это не FirstVDS1,9GiB/swap/диск и не
Sales29Lab16.16. Ширина синтетических строк меньше, чем в production БД14ГБ. Source FK
sale_items→sales валидирован, target — logged таблицы ORM с реальными PK/FK/check/
index. Клиентская таблица заново создаётся между попытками, чтобы статистика не
утекала в следующий baseline. Оба варианта ANALYZE клиентов перед измерением;
work_mem4MB, hash multiplier1, parallel0, temp_file_limit1GB. Baseline180сек только
на стенде, кандидат60сек. Два повтора: baseline/candidate и candidate/baseline.

### Время и точность

| Набор / повтор | Baseline facts, с | Candidate facts, с | Сумма замеренных SQL кандидата, с | Максимальный измеренный SQL кандидата, с | EXCEPT ALL |
| --- | ---: | ---: | ---: | ---: | --- |
| 100k /0 | 2,599 | 7,153 | 2,889 | 0,542 | 0/0 |
| 100k /1, обратный порядок | 2,052 | 8,631 | 2,708 | 0,524 | 0/0 |
| 4,6млн /0 | 63,341 | 104,114 | 62,916 | 3,598 | 0/0 |
| 4,6млн /1, обратный порядок | 63,990 | 199,531 | 73,351 | 3,882 | 0/0 |

На4,6млн:119порций источника,36месяцев,800144группы,4600000строк/единиц,
выручка22953335,61 во всех четырёх попытках. На100k25000продаж/20000пар/2000клиентов,
на4,6млн1190000продаж/73412пар/20000клиентов.

| Отдельный SQL на4,6млн | Повтор0, с | Повтор1, с |
| --- | ---: | ---: |
| Самая долгая порция JOIN | 0,659 | 0,821 |
| Сумма119 порций JOIN | 34,783 | 45,659 |
| Сортировка TEMP-копии | 3,598 | 3,882 |
| Создание индекса даты | 1,238 | 1,259 |
| ANALYZE входа | 0,237 | 0,232 |
| Самый долгий месячный INSERT | 1,083 | 0,830 |

Facts wall включает инструментирование, EXPLAIN/контрольные SELECT и атомарную
запись растущего JSON после каждого плана/пакета. Во втором повторе JSON уже
содержит планы первого; эти накладные расходы существенно растут. Сумма SQL —
отдельно замеренные INSERT/CTAS/DDL/ANALYZE, **не** время production builder:
не включает SELECT границ, estimated EXPLAIN, checkpoints/metadata и внешние API.
Полный боевой снимок на4,6млн с подготовкой пар/Clients API/coverage/публикацией
не измерен. **Ускорение общего времени не заявляется**; измеренный wall вырос.
Показано сокращение длительности отдельных тяжёлых SQL в двух порядках этого
стенда, а не гарантия60сек на FirstVDS.

### Фактические планы и буферы

Во всех119порциях обоих больших повторов sale_items — Bitmap Heap Scan с Bitmap
Index Scan по полуоткрытому sale_id диапазону. Нет Seq Scan sale_items; максимум
40000 Actual Rows × Loops, сумма по всем порциям **4600000**, а не4600000×119.
Максимум370 Exact Heap Blocks и432 Local Read Blocks на порцию; sales — индексный
lookup по sales_pkey, а не полный скан истории. На100k3порции, сумма100000,
максимум40000строк/360heap blocks. Это подтверждено actual plans, а не только
estimated rows/отсутствием названия Seq Scan. Источники лабораторные TEMP, поэтому
локальные buffers не следует называть production shared buffers.

Проверены отдельно Sort входа (external merge при4MB), индекс и ANALYZE: тайм-аут
не перенесён на них. Sort остаётся одним запросом всей таблицы; при большем
размере/медленном диске/другом плане риск60сек сохраняется. Таблицы пар и клиентов
могут читаться повторно для порций; отсутствие полного перескана подтверждено
именно для sale_items, не для всех отношений вообще.

### Temp I/O, промежуточные отношения и WAL

| 4,6млн | Baseline0 | Candidate0 | Candidate1 | Baseline1 |
| --- | ---: | ---: | ---: | ---: |
| Temp Read Blocks инструментированных SQL | 92025 | 166384 | 166373 | 92025 |
| Temp Written Blocks инструментированных SQL | 92143 | 227928 | 228043 | 92143 |
| Executor WAL facts INSERT, байт | 294419083 | 294624390 | 294413125 | 297327973 |
| Cluster WAL delta попытки, байт | 302952112 | 303229240 | 303225328 | 307090888 |

Блок8192байта. Temp — кумулятивные корневые счётчики EXPLAIN ANALYZE staging+facts,
без двойного суммирования дочерних узлов; не включают plain CREATE INDEX/ANALYZE,
setup/EXCEPT и контрольные SELECT. Это **не пиковый объём temp-файлов**. В планах
сохранены Sort Space/Disk, hash batches, Actual Rows/Loops и buffers.
Executor WAL в таблице относится только к facts INSERT (TEMP staging без WAL).
Cluster LSN delta включает все записи кластера/DDL/FPI; не эквивалентна расходу
одного запроса и зависит от checkpoint. На100k temp890/892 против801/803,
executor WAL/все relation samples доступны в summary.

Сырой вход pg_total_relation_size420126720байт, готовый с индексом454131712байт.
Максимум **наблюдавшейся после SQL суммы** обоих входов842186752байт (~803MiB),
когда сортированная копия ещё сосуществует с сырой. Это больше b717e81 (~433MiB)
и **не измерение пика диска**: сортировочные файлы могли существовать одновременно
внутри SQL. После DROP raw остаётся454131712. Target facts116563968байт.
Временный baseline эталон104169472байт есть только в benchmark; в production
вместо него может быть прежний активный снимок другого размера. Temp limit1GB
не ограничивает explicit TEMP relations и WAL. Нужен отдельный подтверждённый
бюджет диска до production; расход RAM/диска FirstVDS этим стендом не проверен.
При53400/нехватке диска не повторять автоматически, не удалять данные и не
увеличивать лимиты. Неудачные logged INSERT могут оставлять dead tuples до
штатной уборки. Никаких автоматических VACUUM/удалений не добавлено.

[summary.json](benchmarks/category_snapshot_chunked/summary.json) и рядом полные
`.json.gz`, SHA256 распакованных оригиналов в summary. В raw ранний header
`input_ordered_by_date=false` не учитывал новый вариант monthly_chunked; это
исправлено в коде после запуска. Raw не переписан: реальный input_sort и его Sort
Key sale_date доказывают порядок. Этот недочёт метаданных не меняет SQL/результат.

## Проверки атомарности

**74 passed,1 deselected,293,62сек**: snapshot plan lab, monthly, build
diagnostics, category seasonality unit/PostgreSQL. Исключён старый
`test_snapshot_benchmark`. Black, AST, bash -n блоков, SHA256 исходных JSON
и git diff --check также успешны.
Новые проверки: timeout57014 и CancelledError во второй порции после реально
заполненной первой, при сортировке, индексе и ANALYZE; первая ручная, повторная
ручная и плановая попытки. Проверяются active/next_due, отсутствие facts и TEMP
после rollback и успешный последующий ручной запуск. Отдельно keyset с разрывами
ID/отрицательным первым ID; полный старый SQL против нового builder на NULL/
пустых значениях/кириллице/ведущих нулях/дробных отрицательных значениях/границах дат.
Полный backend-suite, Docker и FirstVDS здесь не проверены. Лабораторный
benchmark использует psycopg, runtime — asyncpg; планы prepared/generic в
реальном окружении также не следует считать подтверждёнными этим стендом.

## Готовый повтор Sales29Lab

Сначала взять актуальную ветку PR189. Блок создаёт **новую отдельную** лабораторию,
не переиспользует рабочую БД или результаты R8Cabi7p. Используются только локальные
postgres:16-alpine и sales-updater29-test-deps:local, без build/pull, опубликованных
портов, production env и volumes. Если Docker VM storage не виден из WSL,
остановиться и проверить его отдельно. LAB_BASE задать на большом диске стенда.
10GiB — лабораторный admission, не измеренный пик и не лимит production.
До запуска проверить запас RAM для PG1536MiB+runner512MiB+ОС/других задач.

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
LAB_ROOT=$(mktemp -d "$LAB_BASE/sales29-chunked.XXXXXXXX")
PG_NAME="sales29-chunked-${LAB_ROOT##*.}"
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
    --products 10000 --clients 2000 --repetitions 2 --comparison monthly_chunked \
    --physical-target --fresh-client-statistics --baseline-timeout-seconds 180 \
    --sql-timeout-seconds 60 --output /out/chunked-100k.json \
    > "$LAB_ROOT/results/chunked-100k.private.log" 2>&1
small_rc=$?
set -e
printf '100k exit=%s; inspect %s\n' "$small_rc" "$LAB_ROOT/results/chunked-100k.json"
```

После complete/EXCEPT0/0 в обоих порядках и новой проверки RAM/диска вручную:

```bash
df -Pk "$DOCKER_ROOT" "$LAB_ROOT"
awk '/MemAvailable:/{print}' /proc/meminfo
set +e
run_lab --confirm-synthetic-lab --sales 1190000 --items 4600000 \
    --products 36706 --clients 20000 --repetitions 2 --comparison monthly_chunked \
    --physical-target --fresh-client-statistics --baseline-timeout-seconds 180 \
    --sql-timeout-seconds 60 --output /out/chunked-4m6.json \
    > "$LAB_ROOT/results/chunked-4m6.private.log" 2>&1
large_rc=$?
set -e
printf '4m6 exit=%s; inspect %s\n' "$large_rc" "$LAB_ROOT/results/chunked-4m6.json"
python3 - "$LAB_ROOT/results/chunked-4m6.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]));print(r['status'],r['input'])
for x in r['runs']:
 print(x['repeat'],x['variant'],x['status'],x['phase'],x['sqlstate'],
       x.get('elapsed_seconds'),x.get('longest_measured_sql_seconds'),x.get('comparison'))
 for kind in ('input_chunk','input_sort','input_date_index','input_statistics'):
  packets=[p for p in x.get('staging',[]) if p['kind']==kind]
  if packets:
   print(kind,len(packets),max(p.get('elapsed_seconds',0) for p in packets))
PY
```

Проверить все119source ranges по actual Index Cond/rows/loops/heap buffers,
отдельные sort/index/ANALYZE, temp/WAL/storage/effective settings. Не принимать
один успех у60сек за устойчивый результат; при partial сохранять планы/SQLSTATE,
не сравнивать с незавершённым baseline и не повышать кандидат60сек автоматически.
После сохранения артефактов остановить только свой `docker stop "$PG_NAME"`;
данные/образы/результаты сохранить, без prune/down-v/удалений.

## Что остаётся до FirstVDS

PR остаётся draft. Нужен повтор на PostgreSQL16.16/Docker стенде оператора,
проверка ширины/кардинальности/распределения ID и числа позиций на продажу,
плана существующего sale_id индекса и бюджета двух TEMP-копий+spill+WAL+старого
снимка. Только затем ревью и отдельное разрешение на установку готового backend
образа вне VPS; схема29/frontend без изменений. Перед одной следующей fill
оператор проверяет backup/rollback image29, ресурсы, locks/отсутствие импорта,
active_id/next_due. Миграций, stamp, сброса попыток и расписания этот PR не требует.
При отказе rollback транзакции сохраняет прежний active; возврат предыдущего
image29 не требует удаления таблиц/снимков. FirstVDS агентом не затрагивался.
