# Scoped sales and existing admin-review readiness

This audit uses synthetic data only and preserves all source UNKNOWN/SHADOW
restrictions. Production counts and private audit results are not included in
this repository.

New targeted tests:

- `test_real_data_readiness.py`: actual PostgreSQL confirms that a recent sale
  in another store changes the global RFM input while the Aviatorov scoped
  INACTIVITY signal keeps its scoped last purchase; retail and conflicting
  target categories do not enter that scope. It also verifies repeated row
  errors across imports, deliberate skip rules, missing customer/phone handling,
  and a failed header parse with zero processed rows.
- `test_client_review_bootstrap.py`: an opt-in PostgreSQL/HTTP/PHP/MariaDB test
  for the existing admin workflow. Native SHADOW fills the diagnostic inbox;
  admin/CSRF and complete-history confirmation are required. An independently
  authorized producer release uses external ID; the prior normalized-name
  review does not silently grant its binding. The real existing admin APIs
  review that subject and manager designation, policy and budget. Diagnostics
  create zero tasks, explicit synthetic consumer authorization creates one,
  and replay does not duplicate it.

The cross-project test requires the companion Calltrack branch
`audit/churn-real-data-readiness` and its guarded helper
`tests/analytics_review_bootstrap.php`. The local fixture URL, container and
DB names are intentionally fixed, opt-in required. It resets only the named
synthetic `analytics_http_test` database; never use production credentials.

The new test exposed a Calltrack admin validation defect: an immutable
external-ID subject could be saved VERIFIED against a different existing
client ID. Task gates already blocked that mismatch; the companion patch
rejects it before storing the misleading binding.

No new binding model/UI, scoring formula, migration, production configuration
or automatic task policy is introduced. Global RFM remains a display/report
feature, not evidence of store completeness or true client identity.
