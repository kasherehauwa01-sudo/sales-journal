# PR189: готовность monthly_chunked к полному fill

## Что сообщил оператор (не собственный прогон агента)

Sales29Lab, Docker PostgreSQL16.16, код33ed69cb0b445d7daf580c6192c05e433eff2d7d,
1190000продаж/4600000позиций. Исходные отчёты оператора находятся в
`/home/vasiliy/sales29-chunked.3GmI5lGZ/results`; здесь этот каталог недоступен.
[Машиночитаемая фиксация](benchmarks/category_snapshot_full_fill/operator_monthly_chunked.json).

| Порядок | Candidate | Время benchmark candidate | Максимальный SQL | Baseline180сек | EXCEPT ALL |
| --- | --- | ---: | ---: | --- | --- |
| baseline→candidate | завершён | 305,82с | 9,61с, сортировка | 57014 | сравнение недоступно без завершённого baseline |
| candidate→baseline | завершён | 437,00с | 13,71с, сортировка | завершён177,59с | 0/0 |

Оба candidate:119порций, максимум1,06сек на порцию. На100k оба сравнения0/0.
**Partial большого отчёта вызван baseline, не отказом candidate.** Это тест facts,
а не длительность полного `python -m app.category_seasonality fill --confirm`.
Новые оптимизации приложения в этом обновлении не вносились. Runtime-код остаётся
точно33ed69c, SQL60сек, прежние locks/headroom/RR/публикация/REPORT_SETTINGS.

## Что реально проверено агентом 17.11.2026 (Москва)

Docker работает. Локальный postgres:16-alpine оказался **16.15**, зависимого
образа sales-updater29-test-deps:local здесь нет. На диске Docker сначала7,5GiB,
после малых fixtures около6,3GiB. Это ниже консервативного admission16GiB для
полного эксперимента. Чужие данные/образы/volumes не удалялись и ничего не скачивалось.
Большой полный fill на4,6млн здесь **не запускался**.

На Docker16.15 + host Python/asyncpg выполнен функциональный smoke:
1000продаж,4000позиций,200записей локального каталога,20клиентов, реальные миграции
пустой БД до29, реальный stock CLI и localhost HTTP ClientsVR. В том числе два
последовательных CLI-fill проверены автоматическим тестом. Роль sales_fill_app
**NOSUPERUSER/NOCREATEDB/NOCREATEROLE**, владеет созданными её миграциями таблицами.
Нет GRANT/смены роли между проверками до и после fill. Проверены:

- ANALYZE действительно обновил last_analyze, не просто вернул успешный статус;
- тот же app login, REPEATABLE READ, statement_timeout1min, work_mem4MB, hash1,
  parallel0 в момент ANALYZE; ACL/привилегии до и после совпали;
- успешная публикация/истинный next_due по started_at и прежний active сохраняется
  опубликованным при повторном fill; точные units/revenue/lines совпали с источником;
- источник импорта был заблокирован существующим advisory lock (пассивное наблюдение
  и временные границы lock/unlock), без вмешательства в его владение.

**26 тестов прошли за8,97сек**: новый laboratory harness, build diagnostics и
category seasonality unit, включая два реальных малых CLI-fill и оба пути runner
с подставными Docker-командами (успех и отказ16.15 до загрузки). Black, bash -n, AST/git diff --check также выполнены.
Старые74 PostgreSQL-регрессии в этом обновлении не повторялись.
[Smoke-подтверждение](benchmarks/category_snapshot_full_fill/agent_smoke.json).

У host UID нет доступа к Docker-owned PGDATA: observer сообщил PermissionError,
`resource_measurement_complete=false`. Это **не** валидный RAM/disk замер готовности.
CG host-процесса также не эквивалентен отдельному backend-контейнеру.
Времена малого smoke не экстраполируются на4,6млн. Первые dev-ошибки harness
(неоднозначный generate_series smallint и сравнение JSON Decimal со str) исправлены
до итогового прогона; они не являются отказами stock fill и не засчитаны за успех.

## Один полный эксперимент Sales29Lab

`tools/run_snapshot_fill_lab.sh` — единственная поддержанная процедура большого
полного теста. Использует только уже локальные postgres:16-alpine **с server160016**
и sales-updater29-test-deps:local; не выполняет build/pull. Приложение — полный
backend из `git archive 33ed69c backend`, а не переписанный SQL/тестовые ORM-таблицы.
Запуск штатного `app.category_seasonality.main()` с argv fill --confirm и его
обычным asyncpg engine. SQL/helper/API loaders не заменяются. ASGI/lifespan не
запускается: FTP, email, scheduler и прочие фоновые процессы не стартуют.

Реальные Alembic миграции до27, обязательный индекс28 создаётся на **пустой**
таблице, затем миграции до29 и штатная READ ONLY launcher-проверка. Это лаборатория,
не инструкция миграции FirstVDS. Нет stamp/Base.metadata.create_all вместо миграций.
Синтетика создаётся ограниченными SQL-пакетами; local catalog identity вычисляется
штатной функцией по500lookup за пакет. PK/FK/index/числовые типы/проверки настоящие.
Даты2023–2025,1190000sales/4600000items/73412catalog records/20000clients;
код/артикул включают NULL-код и ведущие нули. Width/распределения — синтетические,
не реплика14ГБ базы. Результирующая кардинальность записывается, не задаётся заранее.

Только внешняя зависимость ClientsVR обслуживается реальным localhost HTTP stub
(1manager,1buyer type,20000имён); стандартные HTTP adapters/нормализация/SQL-фильтры
работают без monkeypatch. CatalogVR не нужен: directory читает заполненный local
catalog. Контейнер PG не имеет внешней сети/портов, backend делит только его
network namespace. Настоящие APIs недоступны, секретов production нет.

Эксперимент включает **два последовательных полных CLI-fill**:
1. Первый с active_id/next_due=NULL.
2. Повторный при сохранённом полном активном снимке первого. Это нужно для
   измерения TEMP+WAL+новых данных одновременно с реальным предыдущим снимком.

Оба наблюдают неизменность прежнего active до атомарного переключения и валидный
next_due после публикации. Результаты проверяются **только в новой лабораторной БД**.
Определение next_due не запускает планировщик. При timeout/нехватке ресурсов сценарий
не повышает лимиты, не повторяет fill автоматически и не продолжает второй после
отказа первого. Все данные/отчёты/неуспешные attempts сохраняются.

## Как измеряются время и ресурсы

- `cli_wall_seconds`: весь штатный CLI, включая locks, prepare_pairs, freeze_clients,
  facts, coverage/options, публикацию, retention и dispose engine. Генерация данных,
  запуск HTTP stub и аудит источника после fill не входят. Это отличается от facts benchmark.
- SQLAlchemy listeners измеряют каждый cursor execute и сохраняют max/stage без SQL,
  параметров и клиентских данных. Это не полное время последующего stream fetch;
  `stage_wall_seconds` дополнительно фиксирует работу подготовки/stream/группировок.
  `handle_error` сохраняет stage/SQLSTATE/длительность отказавшего execute.
  Успешный cursor time сам по себе не доказывает длительность всей portal-выборки.
- Импорт: время владения SOURCE_LOCK ограничено снизу/сверху отметками реальных
  acquire/unlock SQL, плюс пассивные pg_locks samples. Не используются пробные
  захваты, которые могли бы помешать самому fill.
- Backend: process RSS VmHWM (ru_maxrss) и kernel cgroup memory.peak, отдельный job
  на каждый fill. PostgreSQL memory.current/peak читает один маленький persistent
  observer в его cgroup каждые250мс. После seed PG останавливается и запускается
  новым контейнером с теми же данными: high-water генерации не выдаётся за fill.
- PG kernel peak включает startup и уже прошедший первый fill для второго замера.
  Сумма отдельных lifetime peaks — **консервативная верхняя граница**, а не точный
  одновременный RSS. Совмещённый sampled estimate выравнивает CLOCK_BOOTTIME в пределах
  0,75сек. Кэш файлов учитывается в cgroup по правилам kernel charging; это не только
  anonymous RSS. После перезапуска часть cache от seed может оставаться charged
  родительской cgroup. Это не полная RAM всей VM; дополнительно фиксируется
  host MemAvailable. Другие процессы и их кэш этими границами не покрыты.
- Каждые250мс считается allocated file blocks всего PGDATA, включая pg_wal,
  base/pgsql_tmp, сырой/сортированный TEMP, indexes, предыдущие/новые snapshots.
  Backend OS-root нужен только для read-only статистики файлов PG-owned directory;
  PostgreSQL соединяется **не superuser**, Docker socket не монтируется.
  Внешние tablespaces/WAL symlink отклоняются. PermissionError не считается нулём.
- `disk_growth_sampled_bytes` — прирост от baseline после seed/restart до максимума
  samples. Это **не строгий filesystem high-water**: быстрые создание/удаление
  между samples можно пропустить. Temp I/O, размер отношений и WAL bytes нельзя
  складывать вместо измерения места. Сроки samples/ошибки сохраняются. Небольшие
  логи/manifest вне PGDATA в эту величину не входят.

Полный отчёт **никогда не ставит ready_for_production=true автоматически**.
Если samples неполны/есть большие пробелы, peak близок к ресурсному бюджету или
max SQL приближается60сек, готовность не подтверждена. Нужен разбор конкретного
`max_sql_stage`, stage_wall/failed_sql, disk/RAM и headroom rejection. Это не повод
менять global GUC, guard, SQL timeout или запускать новую оптимизацию без замеров.

## ANALYZE и роль приложения

Setup admin создаёт новую БД и нового непривилегированного владельца sales_fill_app
на новом пустом PG. Миграции выполняются **этим** app login; fill не получает admin.env,
не делает GRANT и не меняет memberships/ACL. До/после проверяются owner/role flags/ACL
hash и фактическое изменение pg_stat_all_tables.last_analyze. PostgreSQL может
успешно завершить ANALYZE с WARNING "skipping" без нужных прав: одного command tag
недостаточно. Профиль DML-only не выдаётся за подтверждённый app-owner профиль.

Это не доказывает владение таблицей роли FirstVDS: фактические server-only роли
здесь не проверялись. Оператор до установки должен **read-only** проверить под
своей обычной application connection (не выдавая прав):

```sql
SELECT current_user,r.rolsuper,c.relowner::regrole AS table_owner,
       pg_has_role(current_user,c.relowner,'USAGE') AS inherits_owner
FROM pg_class c JOIN pg_roles r ON r.rolname=current_user
WHERE c.oid='public.category_season_clients'::regclass;
SELECT datctype,datcollate FROM pg_database WHERE datname=current_database();
```

`SHOW lc_ctype` для PostgreSQL16 не используется. При несовпадении владельца/ролей
с лабораторным профилем проверку не засчитывать; не исправлять её автоматическим GRANT.

## Запуск на точном коде

Ниже будет закреплён immutable harness commit после сохранения инструментов.
Runtime всегда закреплён на33ed69cb0b445d7daf580c6192c05e433eff2d7d.

```bash
# Только WSL Sales29Lab. Не FirstVDS. Никаких .env/production Docker Compose.
# Вставить закреплённый harness SHA из описания PR; runtime внутри script уже pinned.
export LAB_BASE=/home/vasiliy/sales29-full-fill-lab
mkdir -p "$LAB_BASE"
bash tools/run_snapshot_fill_lab.sh
```

Сначала убедиться, что два локальных образа уже есть и PG действительно16.16.
Script проверяет immutable IDs/архитектуру, cgroupv2, именно DockerRootDir и диск
PGDATA (не место WSL вместо Docker VM). Если путь daemon VM недоступен из WSL,
остановиться и отдельно обеспечить этот preflight внутри VM, не обходить проверку.
Admission16GiB — запас лаборатории, не измеренный пик и не новый лимит production.
RAM defaults: PG1024MiB/backend768MiB; swap сверх RAM для этих двух
лабораторных контейнеров отключён --memory-swap=--memory (host swap не меняется); swap контейнерам не предоставляется сверх
этой RAM. Это лабораторные caps, не параметры FirstVDS. Проверить MemAvailable и
другие нагрузки; значения можно явно согласовать до прогона и сохранить в отчёте.

На success и failure скрипт останавливает **только свои уникальные** контейнеры,
не удаляет PGDATA, файлы, volumes или образы. Контрольный файл observer переводится
в stop, это не recovery/backup marker приложения. При SIGINT/SIGTERM ресурсы
останавливаются, но незаконченный JSON/running нельзя засчитывать за успех.
Ничего не публиковать из *.private.env/*.private.log или полного docker inspect.

## Что передать после лаборатории

`out/manifest.json`, `seed.json`, `first-fill.json`, `retained-fill.json`,
`pg-memory.tsv`, `full-fill-summary.json` (без private env/logs). Они содержат только
синтетические данные/показатели. Проверить:

1. Кандидат33ed69c, схема29, PostgreSQL160016, asyncpg/SQLAlchemy версии, locale catalog.
2. Оба full CLI status=published и next_due_valid; предыдущий full snapshot сохранён.
3. ANALYZE executed=true, права до/после одинаковы, роль не superuser, timeout1min/RR.
4. Все stage times, import lock bracket, SQLSTATE и max cursor SQL с оговоркой stream.
5. Kernel RAM bounds, sampled disk growth/покрытие samples и фактический запас
   относительно доступных **FirstVDS** ресурсов, а не только большой лаборатории.

До этого нет обоснованного вывода, где узкое место полного fill на4,6млн.
Подтверждена только устойчивость facts monthly_chunked в операторском тесте.
FirstVDS/рабочий dump/реальные APIs/миграция/deploy не затрагивались; PR остаётся draft.
