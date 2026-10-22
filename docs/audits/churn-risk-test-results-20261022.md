# Повторные проверки CHURN_RISK, 22.10.2026

Только Codex, синтетические данные. Базы и миграции изолированные. Production не использован.

## Фактические результаты

- Полный набор: **276 passed, 0 failed, 0 skipped**, 47.46s, PostgreSQL **16.14**, MariaDB **11.8.6**. 2 deprecation warnings websockets/uvicorn.
- Предварительный полный набор: 276 passed, 58.52s, PostgreSQL16.15/MariaDB11.4; это повтор тех же тестов, не ещё276 уникальных.
- Version-specific producer/management/migration: 3 passed,14.45s, PostgreSQL16.15/MariaDB11.8.6.
- Расширенный E2E: 1 passed,21.43s, PostgreSQL16.15/MariaDB11.8.6; затем тот же расширенный сценарий вошёл в финальный276 на16.14.
- 7 PHP scripts: PASS; php -l трёх изменённых PHP файлов: PASS.
- Node category regression: PASS.
- Python AST и git diff --check обоих проектов: PASS.
- Первый PHP batch остановился на analytics_sync_test из-за отсутствующего ANALYTICS_ENGINE_EVENT_JSON; повтор с existing synthetic fixture прошёл. Это ошибка настройки вызова тестов; продуктовый код ради неё не менялся.

Тестируются исходники audit/churn-risk-production-readiness поверх Sales4b15629 / Calltrack185bc0a. Изменения формул, config, миграций отсутствуют. Runtime flag monkeypatch включается только внутри изолированного fixture, не production.

## Команда полного финального Python-набора

Тестовый localhost forward55440 ведёт только к churn-audit-pg-1614:5432 во внутренней Docker network churn-phase1-test. PG/tmpfs содержит analytics_shadow_test, rfm_test, calltrack_buyers_test; PHP E2E использует disposable analytics_sync_test на churn-audit-mariadb-1186. Пароль synthetic-only — публичный dummy fixture, не реальные credentials. Не направлять эти команды/DSN на production.

```sh
ANALYTICS_SHADOW_TEST_DATABASE_URL=postgresql+asyncpg://fixture:synthetic-only@127.0.0.1:55440/analytics_shadow_test RFM_TEST_DATABASE_URL=postgresql+asyncpg://fixture:synthetic-only@127.0.0.1:55440/rfm_test TEST_BUYERS_DATABASE_URL=postgresql+asyncpg://fixture:synthetic-only@127.0.0.1:55440/calltrack_buyers_test CHURN_PHASE1_E2E=1 CHURN_TEST_MARIADB_CONTAINER=churn-audit-mariadb-1186 CALLTRACK_ROOT=/workspace/calltrack-verification PYTHONPATH=backend /workspace/sales-journal/.venv/bin/pytest -q --tb=short backend/tests/test_churn_automation_e2e.py backend/tests/test_churn_automation_migration.py backend/tests/test_churn_automation_management.py backend/tests/test_inactivity_shadow.py backend/tests/test_analytics_delivery.py backend/tests/test_stored_sales_shadow.py backend/tests/test_analytics_scope.py backend/tests/test_clients_vr.py backend/tests/test_analytics_freshness.py backend/tests/test_calltrack_integration.py backend/tests/test_calltrack_readonly_http.py backend/tests/test_calltrack_buyers.py backend/tests/test_calltrack_category_details.py backend/tests/test_calltrack_dashboard.py backend/tests/test_calltrack_dashboard_contract.py backend/tests/test_rfm.py backend/tests/test_rfm_snapshot.py backend/tests/test_signal_task_contract_export.py
```

Run cwd /workspace/sales-journal-integration. Все PostgreSQL tests создают собственные guarded schemas и удаляют только свои fixture objects. MariaDB E2E сбрасывает только разрешённую тестовую analytics_sync_test. Не пытаться запускать этот destructive fixture через production host.

## PHP/Node

```sh
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock run --rm --network none --read-only --tmpfs /tmp -v /workspace/calltrack-verification:/code:ro -v /workspace/scratch/synthetic-shadow-event.json:/fixture.json:ro -e ANALYTICS_ENGINE_EVENT_JSON=/fixture.json -e CALLTRACK_STORAGE_DIR=/tmp/test-storage -w /code analytics-php-test:local sh -c 'set -e; for test in manager_bindings analytics_tasks analytics_policy analytics_sync analytics_sync_contract analytics_producer_contract analytics_budget; do php tests/${test}_test.php; done; php -l api/manager_tasks_service.php; php -l tests/analytics_budget_test.php; php -l tests/churn_automation_e2e.php; php api/analytics_sync.php'
```

Ожидаемый и полученный CLI default: `{"mode":"disabled","network_requests":0}`. Fixture не содержит customer data и не входит в commit. Не выдавать этот consumer-only fixture за native producer E2E; настоящий E2E — Python test + native HTTP + MariaDB.

Из Calltrack cwd:

```sh
node tests/client_category_revenue_share_test.js
```

Из каждого cwd:

```sh
git diff --check
```

## Сценарии настоящих БД

| Сценарий | Результат |
|---|---|
| producer feature off / missing signature / unapproved policy | BLOCKED, no release |
| signed complete=false / stale proof / unknown clients / facts/revision mismatch | BLOCKED, no release |
| unknown/ambiguous producer identity / name-phone conflict / aliases | affected candidate excluded; eligible other candidate remains possible |
| preview | no persisted approved events |
| two concurrent release transactions | one RELEASED + one EXISTING |
| consumer disabled / unapproved policy | zero tasks |
| unknown client / no manager mapping / stale sales / UNKNOWN / disabled type | zero tasks and zero spend before approved workers |
| valid approved signal, 3 concurrent workers | exactly1task, correctstablemanager, lifetimecap1 |
| duplicate/replay/completedtask | no newtask or counter spend, DONE/history preserved |
| network interruption | frozen cursor retained; retry succeeds |
| receipt failure after first committed event | page cursor retained; no lost effect/double task on replay |
| budget exhausted/missing | new tasks blocked |
| producer policy/source/import/alias/binding revocation | HTTP410; no old release served on subsequent request |
| local migration | PG new tables upgrade/downgrade, original shadow table retained; checked-in MariaDB SQL |

## Что не доказано

Ни один тест не доказывает правдивость реального upstream manifest, полноту продаж, real client identity/manager assignment, production transport/config/schema/scheduler или бизнес-параметры. Не проверены actual old production image rollback со schema24, полномочия/DDL на текущей production schema, disk/WAL/latency на большом dataset, kill/power/disk-full recovery, все возможные межбазовые revocation races. Поэтому установка disabled и активация имеют отдельные BLOCKED gates в production readiness runbook.
