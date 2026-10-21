# Immutable Analytics delivery (SHADOW)

New protected read-only API under `/api/integrations/calltrack/analytics`: `publications`, `runs/{run_id}/changes`, `latest`. Existing Calltrack Bearer dependency reused. Page size1–100, monotonic publication sequence; no raw phone arrays or full directory allowlist/evidence dumps. Completed run/page payloads are immutable. Normalized subject key is still potentially identifying and remains backend-only, never public.

`analytics_delivery_head` serializes sequence allocation in the same transaction as the existing worker publishes its completed run. `analytics_delivery_publications` links run IDs. The old AnalyticsSignalChange audit ID is only a within-run cursor. New composite run_id/id index bounds page scans. No external writes or task creation happen inside Sales Journal.

Review `20261020_23_analytics_delivery.py` before any application. Worker must be stopped. It bootstraps existing SUCCEEDED runs once and initializes the sequence; does not touch sales or change formulas. Upgrade/downgrade verified only in disposable PostgreSQL schema. Production migration is not authorized. Missing migration must be handled by coordinated disabled deployment, not runtime auto-DDL. Index creation can lock the changes table; inspect its size before production scheduling.

INACTIVITY generator/score/confidence formulas and client_features are unchanged. Existing department/ClientsVR buyer-type filter unchanged. Price types and buyer types have not been equated. Existing source resolver still offers DECLARED/UNKNOWN, does not prove upstream completeness. All exported events retain `mode=SHADOW`, `automation_allowed=false`; no config switch upgrades them into automatic tasks. An explicit source/release contract is still required before real enabled automation.

Calltrack consumer handles immutable publication checkpoint, replay, verified client bindings, explicit manager aliases, approval policy, independent tasks and diagnostics. See Calltrack docs/analytics_tasks.md. Business policy empty/off/unapproved by default; manual tasks independent. Success path for approved envelopes is tested only on synthetic fixtures. Actual SHADOW engine → HTTP export → consumer is tested to remain blocked, including when the consumer mode is enabled.

Tests: `ANALYTICS_SHADOW_TEST_DATABASE_URL=postgresql+asyncpg://sales@127.0.0.1:5432/analytics_shadow_test PYTHONPATH=. pytest -q tests/test_analytics_delivery.py tests/test_inactivity_shadow.py tests/test_analytics_freshness.py tests/test_calltrack_integration.py` from backend. Test URL is guarded to local disposable DB. Optional `ANALYTICS_DELIVERY_FIXTURE=/workspace/scratch/analytics-engine-fixture.json` exports synthetic engine event for PHP boundary tests; never commit runtime dataset/artifacts.

Rollback: disable consumers, stop worker, preserve publications/checkpoints/history; additive tables may remain during code rollback. No production cron/profile is enabled by this change.
