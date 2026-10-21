# Shadow INACTIVITY: результаты для review

База: origin/main `d7b516b06617616c5aa6d0864680760128b04e52`, ветка `feat/analytics-inactivity-shadow`.
Commit/push/deploy не выполнялись. Весь код ниже — uncommitted изменения.

## Git status и полный список файлов

```text
## feat/analytics-inactivity-shadow...origin/main
 M .env.example
 M README.md
 M backend/app/config.py
 M backend/app/main.py
 M backend/app/models/__init__.py
 M backend/app/services/client_identity.py
 M backend/app/services/rfm_data.py
 M docker-compose.yml
 M frontend/src/components/Layout.tsx
 M frontend/src/main.tsx
?? backend/alembic/versions/20261020_21_inactivity_shadow.py
?? backend/app/analytics_worker.py
?? backend/app/models/analytics_signals.py
?? backend/app/routers/analytics_signals.py
?? backend/app/services/analytics_signal_store.py
?? backend/app/services/analytics_signal_worker.py
?? backend/app/services/client_features.py
?? backend/app/services/inactivity.py
?? backend/tests/test_inactivity_shadow.py
?? docs/analytics_signals_shadow.md
?? docs/analytics_signals_shadow_review.md
?? docs/benchmarks/inactivity_shadow_results.json
?? frontend/src/pages/AnalyticsSignalsPage.tsx
?? frontend/tests/AnalyticsSignalsPage.test.tsx
```

## Миграция

`backend/alembic/versions/20261020_21_inactivity_shadow.py`:
`20261015_20 → 20261020_21`.
Добавляет только новые таблицы:
analytics_signal_runs, analytics_signal_state, analytics_purchase_days,
analytics_observed_phones, analytics_client_evaluations, analytics_signals,
analytics_signal_changes. Не сканирует sales, не переписывает историю, не включает worker.
Upgrade → downgrade → upgrade проверен только на localhost analytics_shadow_test.

## Проверки

- Полный backend: **612 passed, 10 skipped**, 1 existing Starlette deprecation warning.
- Отдельный compatibility suite RFM/snapshots/client-insights/client-sales/buyers/category-details/dashboard HTTP: **47 passed, 0 skipped**, PostgreSQL fixtures enabled.
- Frontend: **77 passed** (включая два новых UI tests).
- `npm run build`: успешно; существующее предупреждение о размере JS bundle.
- Новые algorithm/persistence PostgreSQL + unit cases: 21 тест в полном прогоне.
- `git diff --check`: успешно.
- Default CLI `status`: configuration_enabled=false, enabled=false, mode=SHADOW, automation_allowed=false.
- Default CLI `run`: DISABLED, без вычисления.

## PURCHASE_OVERDUE / CHURN_RISK / RESOLVED

Синтетический клиент `client 0`, без телефона: 21 purchase day, 20 интервалов
по 5 дней, span100. UNKNOWN coverage допускает shadow, компонент confidence C=0.
Во всех трёх стадиях один signal_id: `ffa07eb7-4cfa-46d2-a510-de757b89a07f`.

| as_of | type | status | revision | T | median | score | confidence | resolution_reason |
|---|---|---|---:|---:|---:|---:|---:|---|
| 2026-08-09 | PURCHASE_OVERDUE | ACTIVE | 1 | 8 | 5.0 | 34.48 | 0.7556 | — |
| 2026-08-16 | CHURN_RISK | ACTIVE | 2 | 15 | 5.0 | 70.03 | 0.7556 | — |
| 2026-08-17 | CHURN_RISK | RESOLVED | 3 | 0 | 5.0 | 0.00 | 0.7644 | PURCHASE_OCCURRED |

History: CREATED → ESCALATED → RESOLVED. Новая покупка 17 августа; причина
закрытия PURCHASE_OCCURRED. Новый последующий overdue создаёт другой id и
следующий episode_no (проверяется integration test).

Полные JSON signal snapshots, evidence и report row:
[benchmarks/inactivity_shadow_results.json](benchmarks/inactivity_shadow_results.json).

## Shadow report sample на вложенном файле

Источник — локальная копия ранее вложенного HTML, 24 475 продаж. Файл содержит
историю до 06.10.2026; срез 19.10 — диагностический эксперимент, не доказательство
актуального полного источника. Локальный fixture ImportBatch завершён недавно,
поэтому operational freshness HEALTHY; coverage остаётся UNKNOWN.

| Метрика | Значение |
|---|---:|
| Аналитических имён | 3 880 |
| Eligible | 161 |
| Not eligible | 3 719 |
| PURCHASE_OVERDUE | 28 |
| CHURN_RISK | 126 |
| ACTIVE | 154 |
| NORMAL среди eligible | 7 |
| Insufficient history | 3 705 |
| Irregular pattern | 14 |
| Несколько телефонов | 14 |
| Ошибок | 0 |
| Время расчёта | 5.39 с |

UI: `/reports/analytics-signals`, существующий settings-admin gate.
Показывает key/display/phones, eligibility reason, type/status/score/confidence,
last purchase/T/median/mean/thresholds/ratio/gaps/days/MAD/check/revenue,
as_of/coverage/freshness/generator version. Read-only GET, серверные filters и
keyset pagination. В браузер не загружается вся популяция.

## Производительность

На изолированной БД feature provider возвращает 40 клиентов одним SQL statement.
Source scan использует existing sales_pkey (Index Only Scan), 2 000 IDs в
сохранённом EXPLAIN; новых индексов на sales не добавлено. Plans сохранены в JSON.
Это локальный замер на 24 475 строках, не production SLA.

## Ограничения первого релиза

- Нормализованное имя — осознанная primary identity; переименование может разделить
  историю, одинаковые имена — объединить. Телефоны не разделяют/не объединяют ключи.
- Coverage UNKNOWN, не подтверждённая полнота; automation_allowed=false всегда.
- Каждый новый суточный срез — bounded full PK build, не incremental CDC.
- Run projections пока retained; для длительного pilot требуется budget/cleanup.
- Audit IDs не являются готовым внешним feed cursor; Calltrack sync не реализован.
- RFM legacy normalization/mean и integration contracts сохранены.

## Подтверждения

Calltrack исходники не изменены. Production не запрашивался и не менялся.
Миграция и full build выполнялись исключительно в именованных disposable БД localhost.
Ни одна задача/уведомление/звонок не создана. Worker profile opt-in;
ENV default false и durable default false. Внешнего signal feed нет.
Commit, push и deploy не выполнялись. Ожидается review пользователя.
