# STORED_SALES SHADOW: buyer scope v2

Base branch: feat/analytics-calltrack-integration (259ea1d). No formula, identity-normalization version, DB schema, flags or Calltrack change.

## Cause and selection

The v1 loader called get_client_buyer_types(), which fetched /integration/clients and reduced each legacy normalized name to its last buyer type. A full catalogue request (reported ~322655 records) timed out; reduction lost conflicts before analytical normalization.

v2 requests only these existing specialized route names, without public/full-catalogue fallback:
- GET /integration/buyer-types/{URL-encoded Оптовик}/client-names
- GET /integration/buyer-types/{URL-encoded Корпоративный}/client-names

Groups retain raw names until normalize_client_name(..., version=1). Each analytical key accumulates a SET of target kinds. Include exactly one kind; exclude both. Repeated rows do not change membership. Retail is irrelevant to this approved decision and is not downloaded: wholesale+retail is wholesale, corporate+retail is corporate; both target kinds (with/without retail) are ambiguous. Names are not fuzzy-matched or conflated by phone. Lower(), whitespace alphabet and ё/е behaviour remain v1.

SQL department is still Авиаторов. Sales price_type labels are not buyer types. Verified scope empty => worker blocks no-scoped-sales, never broadens to all buyers/stores.

## Proposed strict API contract — NOT confirmed upstream

ClientsVR source checkout is unavailable. GitHub repo discovery through gh repo list returned `Post "https://api.github.com/graphql": Forbidden`; tested ClientsVR/Clients repository URLs were not accessible. No production request was made. The source implementation and deployed response completeness HAVE NOT BEEN VERIFIED.

The following is the contract required by this Sales Journal adapter, NOT a claim about existing ClientsVR responses:

```json
{"clients":["Synthetic Name"],"total":1,"complete":true}
```

- `clients` or `client_names`, exactly one: full array of nonblank strings for the requested buyer kind. Preserve all raw name variations; do not collapse conflicting kinds upstream into one last-wins name map. Duplicate entries are permitted and counted BEFORE de-duplication.
- `total`: nonnegative integer, not bool/string; authoritative total rows for that kind BEFORE pagination, equal to array length. It must not mean page length or count after normalizing/distinct keys.
- `complete`: literal true, only after a successful complete query/serialization. Empty confirmed result is `{clients:[],total:0,complete:true}`. Missing/empty body, bare array, error envelope or missing metadata is not evidence of zero clients.
- Optional `buyer_type` must match the requested kind. Optional `has_more` must be false; next/next_page/next_cursor absent/null/empty; page if given must be1; pages if given0/1. Unsupported/nested metadata is rejected rather than silently ignoring pagination/errors.
- Optional `revision`: nonempty string/integer, excluding bool. If present on either response it must be present and equal on both. No revision means cross-request snapshot consistency is NOT attested. Even a matching revision requires upstream confirmation that it changes on all relevant mutations and supports a consistent snapshot; client-side equality alone is not proof.

Responses with real server-side pagination require an explicit confirmed pagination/snapshot contract and a separately reviewed collector; this implementation rejects partial pages rather than guessing URL parameters or accepting a first page. /clients object responses are deliberately not used as an unverified fallback.

Two-kind bundle is cached only after BOTH requests and validations succeed, in an analytics-only namespace with existing5-minute TTL. A failure on either endpoint rejects the bundle; expired cache is not used as fallback. Existing legacy client/buyer/manager caches and functions are unchanged. TTL implies changes may take up to cache expiry to be observed. Shared cache revision does not prove upstream directory completeness.

## Policy transition

Rules materially changed, so policy is aviatorov-wholesale-corporate/2. Failed-source fallback metadata uses the same version constant.

A running v1 checkpoint whose scope changes is SUPERSEDED before any next scan/evaluate batch; previous publication is retained. If a v1 run is already published, begin_run blocks with ANALYTIC_SCOPE_CHANGED_REQUIRES_ISOLATED_RUN. This PR does NOT clear published pointers, delete history or migrate bindings/episodes. Before rollout the owner must separately choose an isolated validation/transition procedure; resetting state is not prescribed here.

## Safety and readiness

SHADOW only; automation_allowed=false, sales coverage/freshness UNKNOWN remain unchanged. No ClientsVR or sales rows are written, no production DB/API queries, no worker/flag enabling. Read-only GitHub source lookup is not a production call. PostgreSQL opt-in tests remain skipped; new tests are synthetic/mock HTTP and do not use a database.

NOT approved for deploy until ClientsVR code/contract is confirmed to satisfy the strict envelope and complete per-kind selection, response performance is verified through an authorized separate check, and v1→v2 publication transition is agreed. If actual endpoint returns a bare list, old `{clients:[...]}` without metadata or a paginated wrapper, this version safely BLOCKS; it is not a ready production compatibility claim. No upstream server changes are included here.
