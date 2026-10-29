# Report filters: implementation checkpoint

This branch is an intermediate checkpoint, not the complete reports/PDF release.

Implemented:

- Multi-select managers and buyer types in product sales, categories, stores,
  sales dynamics, and promotion candidates. Existing single-value API callers
  remain supported. Values within a filter are combined by union; manager and
  buyer-type filters are intersected.
- Multi-select buyer types in product analytics, including comparison periods,
  product details and Excel. The buyer selection is part of the dataset cache
  key; reordering the same types reuses the same key. Manager and buyer scopes
  intersect, and an unavailable selected source fails with HTTP 502 rather than
  silently generating a partially filtered report.
- Multi-select departments in sales dynamics. Legacy `department` requests remain
  supported; new repeated `departments` parameters take precedence. The selected
  departments and brands apply to the current period and both comparisons.
- Brand selection in product sales, category and store analytics, and sales
  dynamics, including their comparisons, details, and existing Excel exports.
  Promotion candidates and product analytics retain their existing brand filters.
- Brand lookup uses the local, resolved CatalogVR code/article pair. Ambiguous or
  unresolved products do not match a selected brand. Catalog synchronization is
  required for these brand filters; missing attributes are not invented.
- Mixed-brand receipt totals include only matching positions when a brand is
  selected. Unfiltered receipt totals retain their existing meaning.
- Client lists use one PostgreSQL array parameter instead of expanded IN binds
  in the updated reports. Autonomous compilation tests cover 40,000 retail names.
  An ASGI regression sends a retail selection through all seven product-sales
  endpoints (summary, products, dynamics, departments, managers, details, export).
  It checks the real generated asyncpg SQL using 40,001 synthetic client names
  and asserts that bind counts stay bounded; HTTP responses are successful.
  This test uses fake ClientsVR and a compile-checking DB double, not PostgreSQL.
  This addresses a parameter-limit failure mode, not a confirmed diagnosis of
  every possible production HTTP 500.

Remaining original scope:

- PDF download and visual layout.
- Complete filter coverage in RFM, seasonality and the legacy Analytics section.
- Production-specific diagnosis if Retail still fails after updating the reports.

Validation for this checkpoint:

- Targeted backend regression tests: 67 passed, 1 skipped. The skipped test needs
  an isolated PostgreSQL instance; no working database was used.
- Frontend: 84 passed across 8 files, including multi-select snapshot requests
  and preservation of buyer types in product analytics details/Excel after draft
  filters change.
- TypeScript/Vite production build passed. Vite reports the existing large chunk
  warning; frontend tests report an existing table key warning.
- OpenAPI generation, Python compilation and git diff --check passed.
- A broader cache/memory suite attempt timed out at the large cache roundtrip
  case in this environment and is not counted as a pass. Relevant cache-key
  and streamed selection regressions passed in the targeted suite; the full
  cache/memory/concurrency suite was not reverified.
- Mixed-brand SQL behavior exercised using ephemeral in-memory SQLite. This does
  not replace PostgreSQL integration/performance testing on the target schema.

No migrations, production access, deployment, automation or Calltrack changes.
