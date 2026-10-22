# STORED_SALES SHADOW: buyer scope v2

Follow-up branch: fix/stored-sales-client-names-array, based on origin/main b349cd2 (includes PR153 / 8ef0156). No formula, identity-normalization version, DB schema, flags or Calltrack change.

## Cause and selection

The v1 loader called get_client_buyer_types(), which fetched /integration/clients and reduced each legacy normalized name to its last buyer type. A full catalogue request (reported ~322655 records) timed out; reduction lost conflicts before analytical normalization.

v2 requests only these existing specialized route names, without public/full-catalogue fallback:
- GET /integration/buyer-types/{URL-encoded Оптовик}/client-names
- GET /integration/buyer-types/{URL-encoded Корпоративный}/client-names

Groups retain raw names until normalize_client_name(..., version=1). Each analytical key accumulates a SET of target kinds. Include exactly one kind; exclude both. Repeated rows do not change membership. Retail is irrelevant to this approved decision and is not downloaded: wholesale+retail is wholesale, corporate+retail is corporate; both target kinds (with/without retail) are ambiguous. Names are not fuzzy-matched or conflated by phone. Lower(), whitespace alphabet and ё/е behaviour remain v1.

SQL department is still Авиаторов. Sales price_type labels are not buyer types. Verified scope empty => worker blocks no-scoped-sales, never broadens to all buyers/stores.

## Confirmed endpoint shape and validation

The owner inspected the ClientsVR production source in read-only mode and supplied
this contract on2026-10-22. Codex did not access production independently:
`response_model=list[str]`, returning
`list(db.scalars(buyer_type_client_names_query(buyer_type)).all())`.
The query groups by lower(normalized_name), selects min(normalized_name), excludes
archived/empty-name rows, filters Client.buyer_type and contains no LIMIT/OFFSET.

Expected successful response (HTTP200):

```json
["Synthetic Name"]
```

An HTTP200 JSON `[]` is a valid empty selection for that buyer kind, not a network
error. Both empty selections remain an empty scope, which the worker blocks
instead of broadening to all clients. Empty body, JSON null/object/string, blank
name, non-string element, invalid JSON/UTF8, transport error and non200 status
(including206 Partial Content) fail closed. A specialized404/403 never triggers
legacy/public/full-catalogue fallback. The earlier proposed clients/total/complete
wrapper is NOT the real contract and is no longer accepted by this adapter.

Separate raw name arrays preserve cross-kind membership until v1 normalization;
SQL grouping inside each individual kind does not replace the cross-kind set.
The legacy last-wins name map is never used here. Existing generic ClientsVR
request/parsing functions retain their previous behaviour.

Both lists are fetched and validated before creating an analytics-only cache
entry, under a new list-format namespace. Failure on either request rejects the
bundle; expired cache is not used as fallback. Existing caches are unchanged.
The five-minute TTL can delay discovery of upstream membership changes.

### What this does NOT prove

Absence of pagination is a property of the reviewed source, NOT proof of complete
production data or a complete HTTP response. The API supplies no independent
count, completeness marker, checksum or shared snapshot revision. Validation can
reject malformed/truncated JSON and transport-reported truncation, but cannot
detect a valid JSON array that an upstream bug/cache/proxy silently shortened.
Two sequential kind requests are not an atomic directory snapshot; membership
may change between them. The supplied review also does not pin every deployed
endpoint/version or prove completeness of ClientsVR imports/replication.

The owner-confirmed contract permits use for diagnostic SHADOW scope; it does
not promote sales coverage/freshness or enable automation. Sales coverage and
business_freshness remain UNKNOWN, automation_allowed remains false. Real
endpoint latency and end-to-end production/DB behaviour were not tested here.

## Policy transition

Rules materially changed, so policy is aviatorov-wholesale-corporate/2. Failed-source fallback metadata uses the same version constant.

A running v1 checkpoint whose scope changes is SUPERSEDED before any next scan/evaluate batch; previous publication is retained. If a v1 run is already published, begin_run blocks with ANALYTIC_SCOPE_CHANGED_REQUIRES_ISOLATED_RUN. This PR does NOT clear published pointers, delete history or migrate bindings/episodes. Before rollout the owner must separately choose an isolated validation/transition procedure; resetting state is not prescribed here.

## Safety and readiness

SHADOW only; automation_allowed=false, sales coverage/freshness UNKNOWN remain unchanged. No ClientsVR or sales rows are written, no production DB/API queries, no worker/flag enabling. Read-only GitHub source lookup is not a production call. PostgreSQL opt-in tests remain skipped; new tests are synthetic/mock HTTP and do not use a database.

The list-format incompatibility is fixed against the supplied source contract.
No upstream ClientsVR change is required. Deployment still needs separate owner
approval and review of any v1→v2 published-lifecycle transition. No deployments,
real catalogue calls or PostgreSQL integration runs were performed here. The
remaining completeness/snapshot limitations above are not declared resolved.
