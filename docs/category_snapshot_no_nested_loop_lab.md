# Лабораторный no_nested_loop: PR189, без production-изменений

> История первоначальной диагностики/эксперимента35418d9. В текущем draft уже
> добавлен runtime-кандидат с однократным упорядоченным входом и помесячными facts.
> Актуальные результаты100k/4,6млн, ограничения и команды:
> [помесячный кандидат](category_snapshot_monthly_candidate.md). Утверждения ниже
> «production-код не менялся»/«4,6млн ещё не запускался» относятся к прежнему этапу.

## Разделение результатов

**Сообщено оператором Sales29Lab**, не повторено агентом на этом объёме:
1 190 000 продаж,4 600 000 позиций,73 412 пар,20 000 клиентов. Baseline facts
прерван через60,09сек с57014. Оценочный план: Seq Scan sale_items4 599 722,
Hash Join с pairs434, затем два Nested Loop, перед Sort/Sorted Aggregate137.
Actual Rows/Loops у отменённого запроса неизвестны. Отдельный прежний опыт
ANALYZE клиентов на100k:0,975→1,505сек, итоги не изменились. Эти данные не
выдаются за новый прогон и не являются результатом no_nested_loop.

**Выполнено агентом 16.11.2026**:100k на самостоятельном PostgreSQL16.2,
пакет pgserver0.1.4 в /tmp, UTF8/C, localhost, без Docker/рабочего дампа.
Docker-хранилище заполнено: создание даже tmpfs-контейнера и старт прежнего
тестового контейнера отказали с no space left on device ДО SQL. Первичная
настройка отдельного native-сервера с SQL_ASCII также не прошла инициализацию
SQLAlchemy; она исправлена созданием отдельного UTF8-кластера. Успех ниже
относится только к последующему завершённому100k-прогону.

Данные:25 000 продаж,100 000 позиций,20 000 пар,2000клиентов. Два повтора,
порядок baseline/candidate, затем candidate/baseline. Исходный SQL и генератор
данных сохранены;60сек SQL,4MB work_mem, hash multiplier1, parallel0,
lock_timeout2сек, temp_file_limit1GB одинаковы для всех четырёх попыток.

| Повтор | Вариант | Вызов EXPLAIN ANALYZE, сек | Server Execution Time, мс |
| --- | --- | ---: | ---: |
| 0 | baseline | 0,5739 | 566,454 |
| 0 | no_nested_loop | 0,5917 | 590,118 |
| 1 | no_nested_loop | 0,5864 | 584,671 |
| 1 | baseline | 0,6045 | 601,848 |

Во всех случаях:39 285групп,100 000строк-источников,100 000единиц,
выручка497957,5. Двусторонний EXCEPT ALL показал0различий в обоих повторах.
Baseline действительно выбрал1Nested Loop и2Hash Join: Index Scan sale_items
по sale_id имел Actual Rows4, Actual Loops25000. No_nested_loop действительно
выбрал3Hash Join, без Nested Loop/Index Scan. Нельзя переносить эти формы плана
на4,6млн: масштаб и версия PostgreSQL отличаются от Sales29Lab16.16.

У всех четырёх попыток Sort external merge,6408KiB Disk; корневые Temp Read
Blocks801, Temp Written Blocks803 (8192байта/блок). Это I/O, не сумма независимых
файлов; дочерние счётчики повторно не суммируются. Hash Batches1. Полные планы,
Actual Rows/Loops каждого узла, effective settings, spills, сравнения и времена:
[JSON100k](benchmarks/category_snapshot_no_nested_loop_100k.json).
**Ускорение на100k не доказано**: результаты близки, всего2повтора, тёплый кэш,
источники уже production, target FK не моделируются, носитель /tmp — память.

Новый4,6млн-прогон не запускался. Ни PostgreSQL-контейнеры Sales29Lab, ни
FirstVDS агентом не использовались. Ниже — блок для оператора Sales29Lab.

## Как работает эксперимент

По умолчанию сравниваются baseline и no_nested_loop. Последний получает только
`SET LOCAL enable_nestloop = off` в лабораторной транзакции. Перед baseline
явно ставится ON, в том числе после обратного порядка/отказа. Work_mem, temp
и тайм-аут не увеличиваются. OFF — штраф/указание планировщику, не абсолютный
запрет; вывод о соединениях делается по estimated_joins и **chosen_joins**
фактического плана. Исторический вариант доступен через
`--comparison analyze_clients`; он не смешивается с no_nested_loop.

Каждая попытка — SAVEPOINT. При57014/53400 откатывается эта попытка, не подготовка
набора. Вторая проверяется с тем же cutoff/SQL/данными. Файл0600 обновляется
атомарно; estimated plan сохраняется **до** EXPLAIN ANALYZE. Для отменённого
facts actual_plan/actual_nodes=NULL: неизвестные фактические строки не выдумываются.
Для завершившегося SQL есть Actual Rows/Loops, chosen joins, время и spill.

Сравниваются только **два успешно завершённых результата одного повтора**:
EXCEPT ALL отдельно в обе стороны, все измерения/units/revenue/lines, без
служебного auto-ID. Если baseline не завершён, candidate.equivalent=NULL,
comparison_status=not_available. Это не совпадение и не расхождение. При
тайм-ауте самого сравнения phase=comparison, фактический план сохраняется,
но эквивалентность не заявляется. После неудачного первого повтора последующая
успешная пара проверяется отдельно, без использования его частичных данных.

Итог complete означает успешные попытки и точное равенство во всех повторах;
partial/failed дают ненулевой CLI exit, но уже собранные планы не теряются.
Файл running после жёсткого обрыва — незаконченный прогон, не успех. Сбой
соединения/диска, не позволяющий восстановить SAVEPOINT или записать JSON,
не считается проверенным вариантом. Оригинальный SQL/REPORT_SETTINGS и весь
production-код в этом обновлении PR не меняются.

## Готовый блок Sales29Lab: локальные Docker-образы, без сети production

Только WSL2 Sales29Lab, из checkout ветки fix/category-snapshot-first-fill.
Образы должны уже присутствовать: postgres:16-alpine и
sales-updater29-test-deps:local. Никаких build/pull. Нужен Python3 в WSL для
генерации лабораторного пароля. Лаборатория не должна работать на FirstVDS.

Оператор заранее задаёт LAB_BASE — каталог **на диске с достаточным запасом**.
В блоке используется консервативная лабораторная admission-проверка10GiB для
Docker и PG data; это не изменение лимитов приложения и не гарантия достаточности
для любой кардинальности. Проверить свободную RAM также: PG-контейнер имеет
предел1536MiB, Python512MiB, плюс ОС/прочие задачи. Ничего не очищать prune/down-v.
Если DockerRootDir находится в отдельной Docker VM и не доступен из WSL,
остановиться и отдельно проверить свободное место **в Docker VM**, а не выдавать
свободное место WSL-каталога за место Docker. Блок ниже откажет при недоступном пути.

```bash
# Только Sales29Lab. LAB_BASE задать самостоятельно, не production-каталог.
: "${LAB_BASE:?Set a writable large-disk directory on Sales29Lab}"
set +x
set -euo pipefail
umask 077
PG_IMAGE=postgres:16-alpine
PY_IMAGE=sales-updater29-test-deps:local
docker image inspect "$PG_IMAGE" "$PY_IMAGE" --format '{{.Id}}'
DOCKER_ROOT=$(docker info --format '{{.DockerRootDir}}')
test -d "$DOCKER_ROOT" || { echo 'STOP: verify Docker VM storage separately'; exit 1; }
test -d "$LAB_BASE"
for path in "$DOCKER_ROOT" "$LAB_BASE"; do
    df -Pk "$path"
    free_kb=$(df -Pk "$path" | awk 'NR==2 {print $4}')
    test "$free_kb" -ge 10485760 || { echo 'STOP: insufficient lab disk'; exit 1; }
done
awk '/MemAvailable:/{print}' /proc/meminfo
LAB_ROOT=$(mktemp -d "$LAB_BASE/sales29-nestloop.XXXXXXXX")
PG_NAME="sales29-nestloop-${LAB_ROOT##*.}"
LAB_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
mkdir -p "$LAB_ROOT/pgdata" "$LAB_ROOT/results"
printf 'POSTGRES_USER=lab\nPOSTGRES_DB=category_snapshot_lab\nPOSTGRES_PASSWORD=%s\nPOSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C\n' \
    "$LAB_PASSWORD" > "$LAB_ROOT/postgres.private.env"
printf 'LAB_DATABASE_URL=postgresql+psycopg://lab:%s@127.0.0.1:5432/category_snapshot_lab\nDATABASE_URL=postgresql+asyncpg://lab:%s@127.0.0.1:5432/category_snapshot_lab\n' \
    "$LAB_PASSWORD" "$LAB_PASSWORD" > "$LAB_ROOT/client.private.env"
unset LAB_PASSWORD
# No published ports; runner shares only this isolated PG network namespace.
docker run -d --pull never --name "$PG_NAME" --network none --memory 1536m \
    --env-file "$LAB_ROOT/postgres.private.env" \
    -v "$LAB_ROOT/pgdata:/var/lib/postgresql/data" "$PG_IMAGE"
for attempt in $(seq 1 30); do
    if docker exec "$PG_NAME" pg_isready -U lab -d category_snapshot_lab >/dev/null 2>&1; then break; fi
    sleep 1
done
docker exec "$PG_NAME" pg_isready -U lab -d category_snapshot_lab
docker exec "$PG_NAME" sh -c 'df -Pk /var/lib/postgresql/data'
run_lab() {
    docker run --rm --pull never --network "container:$PG_NAME" \
        --memory 512m --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp:rw,size=32m \
        --env HOME=/tmp \
        --env-file "$LAB_ROOT/client.private.env" \
        --env PYTHONDONTWRITEBYTECODE=1 --env PYTHONPATH=/app \
        -v "$PWD/backend:/app:ro" -v "$LAB_ROOT/results:/out:rw" -w /app \
        "$PY_IMAGE" python -m benchmarks.benchmark_snapshot_facts "$@"
}
# Start with100k on this exact Docker stand, even if native100k already passed.
set +e
run_lab --confirm-synthetic-lab --sales 25000 --items 100000 \
    --products 10000 --clients 2000 --repetitions 2 --comparison no_nested_loop \
    --sql-timeout-seconds 60 --output /out/no-nested-loop-100k.json \
    > "$LAB_ROOT/results/100k.private.log" 2>&1
small_rc=$?
set -e
printf '100k exit=%s; results=%s\n' "$small_rc" "$LAB_ROOT/results"
# Inspect both attempts, not just exit status. No secrets or full inspect output.
python3 - "$LAB_ROOT/results/no-nested-loop-100k.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]));print('status',r['status'],'input',r['input'])
for x in r['runs']:
 print(x['repeat'],x['variant'],x['status'],x['phase'],x['sqlstate'],
       x.get('elapsed_seconds'),x.get('estimated_joins'),x.get('chosen_joins'),
       x['equivalent'],x.get('comparison'))
PY
# Review100k and RAM/disk before explicitly proceeding to the next block.
```

Отдельный следующий блок — **ещё не выполнен агентом**. Использует ту же shell-
функцию/переменные из блока выше,1 190 000/4 600 000/36 706/20 000 и оба SQL60сек:

```bash
df -Pk "$DOCKER_ROOT" "$LAB_ROOT"
awk '/MemAvailable:/{print}' /proc/meminfo
set +e
run_lab --confirm-synthetic-lab --sales 1190000 --items 4600000 \
    --products 36706 --clients 20000 --repetitions 1 --comparison no_nested_loop \
    --sql-timeout-seconds 60 --output /out/no-nested-loop-4m6-60s.json \
    > "$LAB_ROOT/results/4m6-60s.private.log" 2>&1
large_rc=$?
set -e
printf '4m6 exit=%s; inspect JSON=%s\n' "$large_rc" "$LAB_ROOT/results/no-nested-loop-4m6-60s.json"
```

У успешного candidate после timeout baseline обязательно сохранить equivalent=NULL,
не сравнивать его с незавершённым baseline. Проверить effective_settings обоих,
estimated/actual join nodes и фактическую кардинальность. При выполнении пары
проверить0в обе стороны comparison. В случае необходимости повторить с
--repetitions2 (новое имя файла), чтобы проверить обратный порядок и тёплый кэш.

Если **оба** facts упали именно с57014: сохранить JSON двух оценочных планов;
Actual Rows/Loops отсутствуют. После новой проверки ресурсов можно отдельно,
осознанно провести ограниченный **лабораторный** замер180сек для обеих попыток:

```bash
# NOT automatic retry, NOT a production timeout recommendation.
run_lab --confirm-synthetic-lab --sales 1190000 --items 4600000 \
    --products 36706 --clients 20000 --repetitions 1 --comparison no_nested_loop \
    --sql-timeout-seconds 180 --output /out/no-nested-loop-4m6-180s.json
```

Значение180 — конечное диагностическое окно3×60, не обоснованный production-
лимит. Work_mem4MB и temp_file_limit1GB не меняются. При53400/нехватке диска/RAM
не лечить ситуацию повышением времени; остановиться и пересмотреть эксперимент.
Не запускаются fill, публикация или расписание. После сохранения результатов
можно остановить **только свой** контейнер `docker stop "$PG_NAME"`; каталог
LAB_ROOT и лабораторные образы остаются, чужие данные/volumes не удаляются.
Не коммитить private.env/полные логи. В PR передать синтетические JSON и краткий
итог; без них4,6млн не считается проверенным.

Актуальное продолжение после тайм-аута staging_input на Sales29Lab:
[порционная подготовка входа](category_snapshot_chunked_candidate.md), draft PR189.
