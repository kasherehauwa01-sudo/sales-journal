# INACTIVITY shadow: freshness and safe analysis date

`imported_at != data_through`, `observed != declared`, `DECLARED != CONFIRMED`.
The PURCHASE_OVERDUE/CHURN_RISK formulas, score weights and confidence weights are
unchanged. Automation is always disabled. This is a SHADOW foundation, not a
completeness guarantee or an automatic manager-task system.

## Business scope

Scope v2 uses **Авиаторов** sales and complete specialized ClientsVR lists for
**Оптовик** and **Корпоративный**. Include a normalized name in exactly one target
kind; exclude the intersection. Retail does not veto a single target kind.
Department AND membership are applied in SQL before features; Sale.price_type
is not a substitute. Names retain analytical normalization v1.

See [scope v2 contract and remaining limitations](analytics_scope_v2.md). The owner
confirmed a specialized HTTP200 JSON list[str] endpoint with no SQL pagination.
Malformed/unavailable/transport-reported partial responses block; no full
/integration/clients fallback. No total, completeness marker or shared revision
is available, so valid-but-incomplete lists and cross-request changes cannot be
independently detected. This does not confirm source completeness/freshness.
Scope is frozen per run, rechecked against the current cached directory before
each resumed batch; changes supersede the checkpoint. A published v1 lifecycle
requires separate isolated validation before policy v2 can publish. No history
or control pointer is automatically reset. Empty scope never means all clients.

## Declared report metadata

ImportBatch has three new nullable fields:

- `declared_period_start`, `declared_period_end`;
- `source_period_method`: `DECLARED_REPORT_PERIOD` for valid known HTML headers,
  `INVALID_REPORT_PERIOD` or `METADATA_EXTRACTION_FAILED` for unusable metadata.

Existing `period_start/end` keep their observed-date semantics (normalized,
non-skipped rows including duplicates); timestamps/status/counters are reused.
The additive migration has no guessed backfill: legacy metadata stays NULL.
ImportOut exposes the new nullable fields, retaining its existing fields.

`app.importer.source_period` extracts metadata separately from sale parsing, from
at most 64 preamble rows, before temporary-source deletion. A recognized sales
header must follow. Known HTML example: `за период с 01.07.26 по 06.10.26`.
Whitespace/NBSP and four-digit years are accepted; known two-digit years mean
20xx. Invalid dates, reversed/contradicting periods and truncated declarations
never fall back to observed dates. Filename dates are not parsed as evidence.
Only HTML/HTM is supported; XLS/XLSX, including a workbook mislabeled HTML, have
UNKNOWN declared period until real workbook contracts are verified. Other row
parsing behaviour, duplicate handling, uploads, FTP and source cleanup remain.

A valid period is a declaration only: the row parser intentionally tolerates
incomplete HTML. Even completed/error-free import does not prove document
completeness. Observed maximum earlier than declared end is valid (empty days);
observed dates outside their own declared range are UNHEALTHY.

## Immutable decision

`app.services.analytics_source.resolve_source` returns frozen decision/evidence:
requested_as_of, last_closed_business_day, declared_data_through,
observed_data_through, accepted_data_through, effective_as_of, coverage_status,
coverage_method, business_freshness, reasons, import IDs/metadata, source revision,
evidence fingerprint, timezone, shadow_allowed and automation_allowed=false.

After all validations:

```
effective_as_of = min(requested_as_of, accepted_data_through, last_closed_business_day)
```

Existing AUTOLOAD_TIMEZONE (default Europe/Moscow) determines the last closed
calendar day: local today minus one. A request for today/future is capped, not
used for recency. Historical requests are retained if inside the accepted range.
There is no unconditional subtraction of one day from a historical declared end.
The file's generation/close time is not supplied by the current contract:
DECLARED coverage consequently never claims that source-day closing or late
correction delivery is confirmed. Explicit missing knowledge remains a limitation.

Coverage states:

| Coverage | New evaluation | Automation |
| --- | --- | --- |
| UNKNOWN | BLOCKED: no honest cutoff, unknown format/legacy metadata/no common range | false |
| DECLARED | Historical SHADOW allowed when health/scope/lifecycle gates pass | false |
| UNHEALTHY | BLOCKED: pending/failed/error import or invalid/contradictory period | false |

Business freshness is separate: STALE if accepted end precedes the last closed
business day, CURRENT otherwise, UNKNOWN/UNHEALTHY when no usable decision.
STALE + DECLARED is a valid historical shadow calculation. Fresh imported_at
never resets business freshness. The old 36-hour configuration remains for
configuration compatibility, but is no longer the INACTIVITY date/eligibility
gate. No CONFIRMED state is introduced.

The pure evaluator's existing HEALTHY argument is used only after calculation
permission passes; its UNKNOWN coverage confidence contribution remains zero.
Persisted evidence/metrics truthfully say DECLARED and business STALE/CURRENT,
not HEALTHY merely because a file was recently received. Formulas are unchanged.

## Multi-source limitation and conservative policy

There is no reliable upstream source_key/required-source registry. We do not
invent one from filename or introduce a large subsystem. All recorded imports
constrain this first policy: each must be technically completed, error-free,
finished and have usable declared metadata. The accepted interval is the
**intersection** of their periods (minimum end, maximum start), not MAX(end).

This is deliberately conservative. An old clean import may cap the date, an old
failed/unknown import can block, and nonoverlapping incremental reports block.
There is no inferred union/continuity or automatic recovery from partial imports.
A legacy database can therefore remain blocked pending a separately approved
source-evidence strategy. No automatic backfill from MAX(sale_date) is performed.
At most 1000 import evidence rows are accepted; larger histories explicitly
block with IMPORT_EVIDENCE_LIMIT_EXCEEDED rather than omitting constraints.
Upstream sources never registered locally are undetectable; DECLARED does not
mean all required stores/documents are present. Report exposes this limitation.

The known Авиаторов filename filter is still only an import filter, not a new
cross-source coverage guarantee. For now unrelated imports can conservatively
limit Авиаторов. A future scoped watermark needs an explicit source contract;
this implementation chooses blocked/older historical results over guessed scope.

## Worker, resume, publication, and existing lifecycle

CLI/direct run_batch request -> resolver -> frozen metadata -> run.as_of
(effective date) -> projection -> features -> unchanged evaluator -> persistence.
HTTP report is GET only and never starts a calculation. Worker remains opt-in,
disabled by environment and durable control by default.

Resume uses original requested/effective dates and buyer membership. If sales
source_revision or import evidence changes, including during evaluation, the run
becomes SUPERSEDED before the next batch/publication. Frozen evidence is not
rewritten. A subsequent run obtains new evidence. Metadata-only changes are
caught by the fingerprint even if sales revision was unchanged. Reuse considers
the full decision, scope and algorithm versions; changing only requested date
still yields truthful evidence.

BLOCKED runs are diagnostic, have zero evaluations and never replace the
published successful pointer or mutate signal lifecycle. Report exposes the
blocked decision beside any previous published results. The legacy NOT NULL
run.as_of has an internal fallback date for blocked runs without a cutoff;
**effective_as_of remains NULL in evidence**, and no features use that fallback.

If safe effective date precedes the last published run (e.g. old 19.10 benchmark,
new cutoff 06.10), block with EFFECTIVE_DATE_PRECEDES_PUBLISHED_RUN. Do not delete,
reopen, backdate or silently recalculate old episodes. Use a separate isolated
comparison database/schema for the historical result. Old in-progress runs with
no safe evidence are superseded. Existing evaluation/audit records remain.
Completed earlier client batches of a subsequently superseded run remain audit
evidence; the run is not published as a new trustworthy population snapshot.

## Late arrivals/corrections

A changed source revision rebuilds purchase-day projections and metrics on the
accepted effective date, even if data_through did not advance. Late purchase
closes an episode only according to its actual date relative to the episode
anchor, not its import time. Prior RESOLVED episodes and audit are retained.

The existing importer is additive: a changed fingerprint can insert another
version; unchanged fingerprint can hide name/item corrections; missing source
documents do not delete prior sales. This change does not implement authoritative
snapshot replacement/document upserts. Neither report period nor revision proves
full correction/removal delivery. Automatic tasks remain forbidden without a
source completeness/correction contract, manifest or control totals.

## Diagnostics and checks

Shadow report shows requested/effective date, last closed day, declared/observed
and accepted cutoff, import timestamp/filename/IDs, business freshness, coverage,
method, date-limitation reasons, and business scope. It explicitly explains
DECLARED is not confirmed completeness and STALE historical calculation is valid.
Buyer membership snapshots stay server-side; report does not download the full
client allowlist.

Tests cover malformed/missing/truncated periods, whitespace, observed/declared
contradictions and empty days, recent old imports, pending/failed/error imports,
multiple bounds, current-day cap, immutable resume and evaluate-phase revision
changes, blocked backward publication, actual upload/cleanup/API metadata,
Авиаторов + both buyer kinds before features, and ClientsVR unavailability.

`docs/benchmarks/inactivity_freshness_results.json` records the isolated real-file
regression. The Козловская file uses an **explicitly unfiltered test-only scope**
for comparability with the accepted baseline: requested 19.10 is capped to 06.10,
yielding 161 eligible, 81 NORMAL, 30 overdue, 50 churn. It is not a benchmark of
Авиаторов/Оптовик/Корпоративный. A synthetic SQL integration test verifies that
business scope; a real Авиаторов benchmark requires its sales and buyer metadata.
The original 19.10 benchmark is retained unmodified.
