# Integration verification — 2026-10-22

## Verdict

Local verification is complete for the checks listed below; production acceptance is **NOT VERIFIED**. No server connection, production query, deployment, migration, analytics calculation, synchronization job or task creation was performed. Existing-client accuracy and authenticated Calltrack UI need a separate controlled read-only verification. HTTP 200 for a missing client is insufficient.

## Versions and local scope

- Sales Journal GitHub main fetched: `3c9d52d8d84e384f5cd4c28ac9b83756e48736ed`. Previous ClientsVR array-contract fix is included. Working branch: `audit/calltrack-integration-verification`.
- Calltrack clean checkout `/workspace/calltrack-verification`: `530adc154ff521e21148473bfae0834430ea9351`, branch `audit/integration-verification`.
- Existing `/workspace/calltrack-integration` has a dangling worktree Git metadata path `/tmp/calltrack-architecture-audit/.git/worktrees/calltrack-integration`; its files were preserved.
- Fetched Calltrack `origin/production/max-message-sync`: `715be8462deaf270c0729310abab25961f91772a`. Relative to 530adc1, Git reports 31 commits only on the integration side and 1 only on the remote production branch. Several integration files are absent on the remote production branch. This does not establish the server's actual HEAD. Do not blindly pull, reset, merge or overwrite that branch.
- No credentials or reachable production environment are configured in this managed environment. PHP CLI is not installed; no dependencies were installed to compensate.

## Confirmed defects and changes

1. **Wrong customer assignment on conflicting sale evidence (high impact).** `find_sales_for_calltrack()` previously chose phone precedence even when the sale's name matched a different key in the same request. It now raises `AmbiguousClientReference`; the existing HTTP handler returns 422 without partial sales. Regression tests cover both service and HTTP behavior. Same-key phone/name matches and name-only/phone-only matches remain supported. No historical records are rewritten.
2. **Stale Calltrack category UI test.** The Node test expected a retired HTML table and failed with undefined `innerHTML`. Updated only the test to assert the current category-tree render contract, lifetime revenue denominator, and invalidation after changing clients. Actual tree behavior was tested separately in Chromium.

Changed application file: `backend/app/services/calltrack_integration.py`.
Changed/new tests: `backend/tests/test_calltrack_integration.py`, `backend/tests/test_calltrack_readonly_http.py`; Calltrack `tests/client_category_revenue_share_test.js`.

## Evidence matrix

PASS below applies to local synthetic tests or source inspection, never an implied production check.

| Component | Check | Result | Evidence |
|---|---|---|---|
| ClientsVR | Scoped name-array contract, conflicting buyer types, failures | PASS | `test_clients_vr.py`, `test_analytics_scope.py` |
| ClientsVR | Actual existing client's identity/manager | NOT VERIFIED | No production access or current private source evidence |
| Sales Journal | Sales matching, dates, duplicate sale IDs, conflicting evidence | PASS | `test_calltrack_integration.py`, HTTP 422 regression |
| Calltrack | Buyers API auth, paging validation, empty vs directory failure | PASS | `test_calltrack_readonly_http.py`; query compilation tests |
| Calltrack | Actual PostgreSQL buyer page/search/count/phone owners | NOT VERIFIED | PostgreSQL test skipped; no isolated DB authorized |
| Calltrack | Analytics cursor pagination/replay, absent run, SHADOW, redaction | PASS | Synthetic HTTP read-only DB adapter; no run generated |
| Calltrack | Persistent consumer replay and exactly-once task behavior | NOT VERIFIED | PHP/MariaDB unavailable; synthetic route tests do not prove consumer persistence |
| Calltrack | Category quantities/revenue, returns, selection, empty results | PASS | Real SQL on in-memory SQLite + aggregate tests; no production data |
| Calltrack | Dashboard year/manager filtering, monthly totals and empty scope | PASS | Actual ASGI route and SQLite SQL in cross-repository contract test |
| Calltrack | Category UI lazy details, retry, duplicate requests, stale replies, escaping | PASS | Isolated Chromium execution of first section of existing tree browser test |
| Integration | Real existing client → ClientsVR → sales/categories/RFM → authenticated UI | NOT VERIFIED | Required acceptance criterion remains open |
| Safety | SHADOW and automation prohibition in candidate | PASS | API tests, source defaults and gate inspection; no flags changed |
| Safety | Current production runtime flags and scheduled jobs | NOT VERIFIED | Previously supplied observations are not a current runtime inspection |

## Executed checks

From Sales Journal:

```sh
env -u ANALYTICS_SHADOW_TEST_DATABASE_URL -u TEST_BUYERS_DATABASE_URL \
  CALLTRACK_ROOT=/workspace/calltrack-verification PYTHONPATH=backend \
  /workspace/sales-journal/.venv/bin/pytest -q -rs \
  backend/tests/test_calltrack_integration.py \
  backend/tests/test_calltrack_readonly_http.py \
  backend/tests/test_calltrack_buyers.py \
  backend/tests/test_calltrack_category_details.py \
  backend/tests/test_calltrack_dashboard.py \
  backend/tests/test_calltrack_dashboard_contract.py \
  backend/tests/test_analytics_delivery.py \
  backend/tests/test_clients_vr.py backend/tests/test_analytics_scope.py \
  backend/tests/test_stored_sales_shadow.py backend/tests/test_inactivity_shadow.py \
  backend/tests/test_analytics_freshness.py
```

**235 passed, 17 skipped.** Skips: buyer PostgreSQL test (1), category PostgreSQL test (1), inactivity/delivery PostgreSQL-dependent tests (15).

Calltrack Node tests, each executed successfully after repairing the stale category test:
`client_sales_grouping_test.js`, `client_category_revenue_share_test.js`, `client_history_summary_test.js`, `client_workspace_selection_test.js`, `purchase_money_test.js`, `client_email_timeline_test.js`, `max_message_history_ui_test.js`.

Chromium: ran only the self-contained first section of `client_category_tree_browser_test.py`, before `# Shared actual client modal`. It uses synthetic DOM/data, no HTTP server. The subsequent authenticated PHP application smoke section was **not run**.

`git diff --check` on both repositories: passed. The initial audit did not create commits, pushes, PRs or deployments. Publication preparation is a separate authorized step; production acceptance remains NOT VERIFIED.

## Identity limitations — do not equate tests with verified identity

Sales rows do not carry a verified ClientsVR ID through these read APIs. Legacy exact-name/phone matching is still present. The new contradictory-evidence guard only catches disagreement between distinct keys in a request. It cannot distinguish two ClientsVR entities already collapsed into one key upstream.

Calltrack `canonicalizeSalesJournalClients()` joins groups by shared name or phone; `clientIntegrationKey()` prefers phone even when an ID exists. Therefore a shared phone/name across distinct real ClientsVR IDs remains a **business correctness risk**, not an identity guarantee. The buyers contact path is more conservative (unique directory name, owner and phone checks), but does not establish all other entry paths are safe. A broad identity-model change was not made without runtime verification. Test a collision case privately before accepting real-client correctness.

ClientsVR specialized name endpoints return no source count/revision/common-snapshot proof. JSON shape validation catches malformed or failed requests, not silently shortened valid arrays or changes between the two requests. The local 300-second cache also means membership is not an instantaneous directory snapshot.

## Remaining PostgreSQL/PHP risks

Not verified here: PostgreSQL regex/collation behavior, ANY-array binding, actual buyer counts and plans, streaming transaction isolation, advisory/row locks, failure rollback and resume, publication atomicity under concurrency, migration/runtime schema compatibility, PHP ACL/session behavior and persisted consumer deduplication. SQLite and mocked reads do not prove these properties.

SHADOW calculations, if deliberately enabled later, write local analytics runs/signals/evaluations/publications and take locks; SHADOW is not read-only. Calltrack dry_run synchronization can persist inbox/rejection/cursor/cache state even though it must not create tasks. Neither calculation nor synchronization was invoked here.

## Next controlled read-only acceptance

1. Collect fresh server Git HEAD/status for both actual project paths, backend image ID/start time, effective flags and schema revision. Compare files against approved candidates; preserve backups and unrelated untracked files. Remote branch contents alone do not identify running code.
2. Select one real existing client privately in Calltrack; record its ClientsVR ID, allowed manager, names/phones and chosen period in private evidence, not public logs or commits.
3. Use the existing authenticated UI and read endpoints. Confirm the ID selection maps to that entity; shared-name/phone cases must not silently combine different IDs. Verify manager access with an authorized account and a non-owner account.
4. Compare exact sale IDs, dates, document numbers, totals and purchase count against source rows for that client and period. Compare category/product quantity and line-price sums, including returns. Header revenue and item revenue can differ; explain actual differences rather than forcing equality.
5. Verify RFM against the published snapshot's own period/calculation time, not an unrelated current interval. Check snapshot unavailable (503), client absent from snapshot, and stale state visibly handled.
6. Read existing analytics publications and pages twice; compare stable run IDs/cursors without running a worker, publishing, or invoking Calltrack synchronization. Empty feed is valid evidence only of empty-feed behavior.
7. Save a private screen-level comparison of the actual Calltrack card and API/source values. Only then can E2E be marked PASS. If any identity/data mismatch occurs, stop acceptance and provide redacted structural evidence for a targeted fix.

The local backend fix would require separately approved deployment to take effect. No infrastructure/flag/migration change is necessary for the fix itself. Full integration acceptance remains blocked on the real-client/server checks above.
