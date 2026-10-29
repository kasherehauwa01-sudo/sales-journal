# Report filters: implementation checkpoint

This branch is an intermediate checkpoint, not the complete reports/PDF release.

Implemented:

- Multi-select managers and buyer types in product sales, categories, stores,
  sales dynamics, and promotion candidates. Existing single-value API callers
  remain supported. Values within a filter are combined by union; manager and
  buyer-type filters are intersected.
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
  This addresses a parameter-limit failure mode, not a confirmed diagnosis of
  every possible production HTTP 500.

Remaining original scope:

- PDF download and visual layout.
- Complete filter coverage in RFM, seasonality and the legacy Analytics section.
- Production-specific diagnosis if Retail still fails after updating the reports.

Validation for this checkpoint:

- Targeted backend regression tests: 36 passed, 1 skipped. The skipped test needs
  an isolated PostgreSQL instance; no working database was used.
- Frontend: 82 passed across 8 files, including multi-select snapshot requests.
- TypeScript/Vite production build passed. Vite reports the existing large chunk
  warning; frontend tests report an existing table key warning.
- OpenAPI generation, Python compilation and git diff --check passed.
- Mixed-brand SQL behavior exercised using ephemeral in-memory SQLite. This does
  not replace PostgreSQL integration/performance testing on the target schema.

No migrations, production access, deployment, automation or Calltrack changes.
