# Сценарий «Сигнал Риск ухода»: мониторинг и безопасное восстановление

## Что реализовано

`Настройки → Сценарии` содержит отдельную карточку и страницу
`/settings/scenarios/churn-risk`. HoReCa остаётся существующим email-сценарием:
его таблицы, scheduler, отправка, ссылки и CRUD не меняются.

`GET /api/settings/churn-scenario/status` защищён существующей сессией
администратора настроек. POST/PUT/DELETE для запуска, утверждения, изменения
лимитов или отправки нет. GET никогда не вызывает worker, preview/release,
Calltrack HTTP, shell, systemd или чтение приватных ключей. Все DB-запросы read model —
SELECT; существующий общий аудит mTLS остаётся без изменений; на PostgreSQL применяются REPEATABLE READ / READ ONLY, statement timeout 3 с и lock timeout
500 мс, общий read model ограничен 5 с. Транзакция откатывается даже при отмене.

API проецирует необходимые поля существующих моделей: policy, signal state/run,
release/events, decisions, identities, source revision и наличие pending import.
Не загружает source purchase days, продажи, списки имён, подписанные manifests,
подписи, observed phones или ключи субъектов. История ограничена 20 расчётами,
30 публикациями/наблюдениями в ответе, список непроверенных привязок — 50 ID.
Вычисления и существующие механизмы допуска не меняются. Миграций нет.

## Значение показателей

- Проанализированные/рисковые клиенты — сохранённые counters опубликованного run,
  не все клиенты справочника. Отсутствующий counter — null, не выдуманный ноль.
- «Готово» — последняя сохранённая проверка допуска этого run с её датой, не
  новое разрешение и не повторный preview. Отдельные текущие блокировки сохраняются.
- «Опубликовано» — события связанного совместимого AUTOMATION_APPROVED release.
  Старая `/1`, другой run, другая policy revision и SHADOW не подменяют `/2`.
- «Принято» — receipt соответствующего release_id из фактического inbox Calltrack
  с TASK_CREATED/TASK_EXISTS. Без такого наблюдения — null и DELIVERY_UNCONFIRMED.
- «Связанные задачи» — distinct task_id у этих receipt; это отдельное число.
  Не складывать его с сигналами и не суммировать задачи повторно по публикациям.
- Read-only receiver summary относится к последнему наблюдению inbox. Inbox хранит
  текущую ревизию сигнала; это не полный неизменяемый журнал всех прежних приёмов.
  Старое receipt может перестать быть наблюдаемым после новой ревизии; мониторинг
  не заявляет нулевую историческую доставку и не выполняет повторную отправку.
- Ошибки показывают известный технический код, компонент, время наблюдения,
  влияние и рекомендацию. Сырые журналы/exception strings не возвращаются.
- VERIFIED, preliminary и pending отображаются отдельно. Проверка привязки
  выполняется прежней процедурой: ID, вся история, менеджер, уникальность и
  конфликты; массового подтверждения нет. Непроверенные ID доступны только admin.
- «Включён» — все доступные проверки мониторинга удовлетворены, а не разрешение
  создавать задачи. Отключение flag/политики, ошибки и неизвестное состояние
  отражаются отдельно. SHADOW расчёт и approved-выдача не смешиваются.

## Источник systemd и Calltrack: без привилегий в backend

Backend читает `/run/sales-churn-monitor/status.json`, максимум 128 КиБ: обычный
root-owned файл, без symlink/FIFO/device и записи group/world. Неизвестная схема,
лишние поля, некорректные даты, возраст более 36 часов или дата из будущего
отклоняются. Не выдавать backend Docker socket, sudo, доступ к журналам/ключам.

Снимок подготавливает `backend/scripts/collect_churn_status.py` на host:
- только `systemctl show` двух фиксированных существующих unit и `journalctl`
  этого сервиса, максимум 100 записей; ограничены время/размер stdout/stderr;
- optional MariaDB CLI с защищённым defaults-файлом SELECT-only аккаунта,
  одной READ ONLY транзакцией и aggregate SELECT по небольшим таблицам
  автоматизации. Продажи и история товаров не сканируются;
- проверка явного file→SHA256 manifest без изменения контрольных сумм;
- optional чтение allowlist `approved_generators` из защищённой consumer policy;
  никакие другие ключи policy не включаются в снимок;
- атомарная замена только собственного файла наблюдений, mode 0640, group
  родительского каталога. Исходные журналы/бюджеты/manifest/сигналы не меняются.

Это **наблюдение на указанное время**, а не live systemd. «Обновить статус»
перечитывает PostgreSQL и последний защищённый снимок; не запускает host helper.
Дата наблюдения видна. При смене московского дня вчерашний дневной used/remaining
не объявляется сегодняшним. При отсутствии источника — «Нет данных», не ноль.
Время истории внешнего пилота — время наблюдаемой записи; полного старта/успеха
из усечённого journal при отсутствии фактов не выдумываем.

Сборщик не знает содержимое server-only pilot.py и не подменяет его. Пример
ExecStopPost использует существующий service и не добавляет второго таймера.
Во время stop-hook сам unit может ещё иметь deactivating; снимок показывает
именно наблюдаемое состояние. Нельзя выдавать его за последующее live-состояние.
Для окончательного состояния оператор может отдельно вызвать тот же read-only
сборщик после завершения сервиса. Сам сборщик задач не создаёт.

## Что нужно проверить на VPS (не выполнено)

Безопасные команды владельца, без печати Environment/ключей/политики целиком:

```bash
systemctl show calltrack-churn-pilot.service \
  -p FragmentPath -p DropInPaths -p User -p Group -p ActiveState -p Result \
  -p ExecMainStartTimestamp -p ExecMainExitTimestamp
systemctl show calltrack-churn-pilot.timer \
  -p FragmentPath -p UnitFileState -p ActiveState -p TimersCalendar -p NextElapseUSecRealtime
sha256sum /var/www/html/vr/calltrack-v3-20261026/api/manager_tasks_service.php
sha256sum /usr/local/libexec/calltrack-churn-pilot.py
sha256sum /etc/sales/churn-pilot/runtime-code-sha256.json
stat -c '%a %U %G %n' /etc/sales/churn-pilot /etc/sales/churn-source \
  /usr/local/libexec/calltrack-churn-pilot.py
```

Не публиковать systemctl Environment, содержимое private keys или неочищенный
journal. Не считать найденный GitHub source проверкой всей серверной папки.
Аудит двух PHP-версий и контрольные SHA256: `audits/churn-integrity-20261030.md`.

В PostgreSQL выполнить точечное чтение в короткой READ ONLY транзакции:

```sql
BEGIN READ ONLY;
SET LOCAL statement_timeout = '3s';
SELECT enabled,approved,revision,
       settings_json->>'algorithm_policy' AS algorithm_policy,
       settings_json->>'min_confidence' AS min_confidence
FROM analytics_automation_policy WHERE id=1;
SELECT status,evidence->>'stage' AS stage,count(*)
FROM analytics_automation_identities GROUP BY status,evidence->>'stage';
SELECT metadata_json->>'algorithm_policy' AS algorithm_policy,count(*)
FROM analytics_automation_releases GROUP BY metadata_json->>'algorithm_policy';
SELECT count(*) FROM analytics_automation_events;
ROLLBACK;
```

Проверить фактическую схему V3 до применения запросов. В MariaDB через
существующий разрешённый read-only доступ:

```sql
SET TRANSACTION READ ONLY;
START TRANSACTION;
SELECT approved,max_tasks,used_tasks,revision FROM analytics_task_budget WHERE id=1;
SELECT policy_version,timezone,max_tasks FROM analytics_daily_budget_policy WHERE id=1;
SELECT business_date,used_tasks FROM analytics_daily_budget ORDER BY business_date DESC LIMIT 7;
SELECT business_date,COUNT(*) AS reservations FROM analytics_daily_budget_reservations
GROUP BY business_date ORDER BY business_date DESC LIMIT 7;
SELECT COUNT(*) AS automatic_tasks FROM manager_tasks WHERE source_signal_key IS NOT NULL;
ROLLBACK;
```

У daily budget, lifetime budget и cap по менеджеру разные основания.
`total lifetime budget exhausted` нельзя исправлять очисткой reservations,
used_tasks или увеличением лимита без нового отдельного утверждения.

## Развёртывание мониторинга — отдельное действие администратора

1. Сверить текущие code/image/schema и все server-only расширения V3. Собрать и
   проверить backend/frontend последовательно вне ограниченного VPS. Dockerfile
   нормализует только каталоги кода и Python-файлы/startup, а не .env,
   ключи, configs или storage. Runtime остаётся UID 10001; chmod 777 нет.
2. Использовать существующую проверенную процедуру update_sales.sh с прежним
   порядком override-файлов, churn, Calltrack и mTLS. Источник рабочего update_sales.sh
   этой работой не получен и не изменён. Для Dockerfile/API/frontend его изменение
   не требуется: нужен именно новый образ, а не только git pull. Если реальный
   updater не поддерживает эту сборку, сначала аудит его копии, не слепой патч.
3. Пример `deploy/churn-monitor/backend-mount.example.yml` — одна read-only запись
   volume для существующего последнего override. **Не заменять** его services,
   volumes или image этим примером. Либо отдельно согласовать добавочный override
   и изменение списка файлов updater по реальному исходнику. Монтировать каталог,
   не отдельный файл: atomic replace снимка должен оставаться видимым в контейнере.
4. После разрешения установить ограниченный сборщик и подготовить root-owned
   каталог с group существующей SALES_BACKEND_GID; пример, значения проверить:

```bash
install -m 0755 backend/scripts/collect_churn_status.py /usr/local/libexec/sales-churn-status.py
install -d -o root -g "$SALES_BACKEND_GID" -m 2750 /run/sales-churn-monitor
```

   `/run` очищается после reboot. Пример `deploy/churn-monitor/tmpfiles.conf.example`
   требует подстановки проверенного имени backend-группы, не автоматического 777.
   Отсутствующий каталог остаётся блокировкой; сборщик не создаёт широкие пути.
5. Сначала запустить **только read-only наблюдение**, без изменения timer/pilot:

```bash
/usr/bin/python3 /usr/local/libexec/sales-churn-status.py \
  --output /run/sales-churn-monitor/status.json \
  --manifest /etc/sales/churn-pilot/runtime-code-sha256.json \
  --calltrack-root /var/www/html/vr/calltrack-v3-20261026
```

   Без receiver-defaults точные лимиты/receipt неизвестны. Для них администратор
   предоставляет SELECT-only аккаунт и root-only mode 0600 defaults-файл с database,
   host/user/password. Используется --defaults-file, без слияния с другими client defaults; subprocess не наследует секреты окружения сервиса. Нельзя импортировать PHP bootstrap с потенциальными DDL.
   Дополнительные параметры сборщика: `--receiver-defaults /защищённый/файл.cnf`
   и `--receiver-policy /фактический/consumer-policy.json`. Не передавать пароль
   в argv/URL, не публиковать defaults/policy. Схема и право READ ONLY должны быть
   проверены по реальному MariaDB; нативное исполнение этих запросов здесь не выполнено.
6. Проверить снимок и API, затем вручную согласовать пример
   `service-monitoring.conf.example` как drop-in существующего service. Он только
   наблюдает при завершении, не меняет ExecStart/OnCalendar и не включает таймер.
   Сверить User/Group, filesystem sandbox, порядок ExecStopPost и таймауты реального
   unit. Если существующий процесс не root, НЕ добавлять `+` и новые привилегии
   автоматически. Согласовать иной доверенный способ сбора отдельно; API остаётся
   без привилегий. Сам образец не является готовым проверенным патчем неизвестного unit.
7. daemon-reload/пересоздание backend с томом — только по отдельному разрешению.
   Проверить /api/health, страницу и отсутствие секретов. Старый backend отвечает
   404 на новый endpoint: frontend явно предлагает обновить backend, HoReCa работает.

Нового таймера, automatic enable, policy approval, пересчёта, сигнала или задачи
ни установка страницы, ни GET статуса не создают.

## Восстановление бизнес-автоматизации: после мониторинга, не автоматически

- Сверить алгоритм /2 с примерами 90/50 и 90/103. Создать новую ревизию политики
  через существующие policy-draft/policy-approve с expected revision и явным
  утверждением. Не делать SQL replace /1→/2 и не переносить старые публикации.
- Подтвердить источник/актуальность, ID/историю/менеджера и конфликты через текущую
  процедуру. PRELIMINARY_MATCH не массово переводить в VERIFIED. Сверить server-only
  signed preliminary bridge отдельно: его реальное поведение здесь неизвестно.
- Проверить совместимость /2 и подписанных envelope на реальном Calltrack/V3.
  Root-owned monitoring JSON НЕ является подписанным разрешением или source proof.
- Пройти контрольную процедуру manifest из аудита. Подготовка patch на копиях:

```bash
python3 backend/scripts/prepare_churn_integrity_patch.py \
  --manifest-copy /защищённая/копия/runtime-code-sha256.json \
  --php-copy /защищённая/копия/manager_tasks_service.php \
  --reviewer 'идентификатор-проверяющего' --output /защищённая/копия/review.patch
```

  Инструмент не обновляет manifest. Точные live bytes, формат и непрерывность
  защиты всех прочих файлов остаются обязательной проверкой администратора.
- Бюджеты не сбрасывать. Новый лимит — отдельное бизнес-утверждение с сохранением
  счётчиков и ledger; при исчерпании пилот должен остаться заблокированным.
- Только после всех условий отдельно разрешить штатный существующий ежедневный
  процесс на 09:00 Europe/Moscow. Проверить повтор, восстановление, слоты/бюджеты,
  новый purchase и отсутствие повторного создания задач. В этой работе создание
  задач или запуск production worker не разрешались и не выполнялись.

## Откат

Вернуть предыдущие согласованные backend/frontend образы через существующий
updater, сохранив весь набор override, данные, публикации и backup. Миграций нет;
не применять downgrade/stamp. Read-only том и новый monitoring drop-in можно
убрать отдельно после согласования; не редактировать ExecStart существующего
пилота и не трогать его timer при обычном откате интерфейса. Не удалять volumes,
БД, каталог, source keys, manifests и резервные копии.

Если manifest был отдельно изменён администратором, откат этой операции требует
согласованного сочетания PHP bytes и проверенных hash; нельзя просто вернуть
старый hash к новому файлу и считать интеграцию исправной. Безопасный отказ guard
лучше обхода защиты. Мониторинг не меняет policy revision, бюджеты или события,
поэтому они не требуют отката вместе с интерфейсом.

## Дополнительные условия review

Снимок получателя — наблюдение БД inbox/budget и существующей policy, не проверка
HTTP-dashboard Calltrack. Доступность HTTP без отдельного проверенного endpoint
не предполагается. Ошибки связи/авторизации из фактических сообщений пилота
отображаются отдельно. Успешный SQL SELECT не объявляется успешной HTTP-отправкой.

Новые main.py/config.py и модули мониторинга тоже могут входить в серверный
runtime-code manifest. Если он охватывает их, необходимо отдельно проверить
все эти изменения и обновить только согласованные записи. Подготовленный
PHP patch не является разрешением обновить остальные hash автоматически.

Публичная CLI сборщика разрешает запись только в выделенный
`/run/sales-churn-monitor/status.json`, не в произвольный файл host. Контрольный
JSON требует root owner; backend имеет только read-only mount. Образ Docker
в этой работе не собирался: проверка прав выполнена на синтетическом дереве
реальными find/chmod; реальные nonroot import/image checks нужны перед deployment.

## Примеры существующих команд утверждения (не выполнены)

Сначала получить фактическую policy revision и подготовить защищённый,
проверенный файл настроек `/2`, сохранив согласованные confidence, issuer,
источники и срок подтверждения. Значения не подставлять из тестов. На
согласованном окружении, только после отдельного разрешения:

```text
python -m app.analytics_automation policy-draft --input /secure/reviewed-v2-policy.json --actor REVIEWER --revision ACTUAL_REVISION
python -m app.analytics_automation policy-approve --input /secure/explicit-approval.json --actor REVIEWER --revision RETURNED_DRAFT_REVISION
```

`explicit-approval.json` содержит `{"confirm_business_approval":true}`; файл draft
и его изменение версии требуют предварительного review. Draft отключает и
снимает утверждение старой политики; approve — отдельное действие с новой
ревизией. При конфликте revision остановиться, не перезаписывать состояние.
CLI — текущая repository-процедура; перед переносом проверить отличия server V3.
Не выполнять эти команды как часть сборки, установки страницы или GET статуса.

## Результаты изолированной проверки

- Backend: **944 passed, 46 skipped**, 15,79 с. Включая **51** новую проверку
  мониторинга/инструментов и существующие проверки алгоритма, сценариев HoReCa,
  ссылок, импорта, auth, каталогов и отчётов.
- Frontend: **108 passed**, 11 файлов, 9,40 с; TypeScript и Vite build проходят.
- Существующие предупреждения Starlette/AnyIO и Vite о размере общего bundle
  не устранялись этим изменением. Зависимости/lock-файлы не менялись.
- SQL read model исполнялся на одноразовой SQLite; JSON GROUP BY дополнительно
  компилировался диалектом asyncpg с общим bind для SELECT/GROUP BY.
- Отдельный test-run guard запрещал исходящие Python socket connections, кроме
  локальных/Unix; он находится вне приложения. Фикстуры — синтетические.
- Обновляющий Bash-скрипт, Docker, systemd, MariaDB и production не запускались. Локальные
  дочерние Python-процессы проверяют bounded pipes/таймаут/cleanup, а patch
  --dry-run работает только с временной синтетической копией. Права find/chmod
  проверены на синтетическом дереве; .env, private.pem/keys и alembic.ini не
  расширяют права. Docker image import и настоящий unit требуют отдельной проверки.
- Скipped проверки требуют опциональных изолированных PostgreSQL/Docker/
  PHP-MariaDB стендов. Они не считаются пройденным подтверждением native locking,
  server-only V3 или end-to-end создания задач.

Мониторинг готов к review и согласованной установке. Восстановление production-
автоматизации, применение manifest patch, новая policy revision и лимиты не
объявляются выполненными результатами этих тестов.
