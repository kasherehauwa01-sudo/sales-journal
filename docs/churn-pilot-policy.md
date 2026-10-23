# Aviatorov pilot policy v1

PRODUCTION DEPLOYMENT = BLOCKED — NO VERIFIED RECOVERY.

Generator `inactivity/aviator-churn-pilot/1`, parameters version 2, business policy
`aviator-churn-pilot/1`. Scope remains `aviatorov-wholesale-corporate/2`: department
Авиаторов, ClientsVR buyer_type Оптовик/Корпоративный, both target types ambiguous
and excluded. price_type is not used as buyer_type.

Only this exact scope selects the pilot. Legacy `inactivity/1` evaluator is unchanged
and remains available for other scopes. The pilot emits CHURN_RISK only, at >=45
calendar days since the last positive store purchase, with >=3 distinct positive
purchase days within the existing 365-day observation window. No cadence, MAD or
history-span eligibility limits apply to this pilot. Counts aggregate checks by
client/day: three receipts on one day count once. Zero/negative receipts and known
excluded document prefixes ВЗВ-/РНВ-/ВРМ- cannot add a positive day. A positive
purchase and a return on one day still constitute a positive purchase day; net
revenue remains unchanged. Unknown upstream return encodings require source review.
Other-store sales do not add days or reset the store anchor. Global RFM is not used.

Descriptive interval statistics and commercial score components are retained.
Confidence retains its old quality model and UNKNOWN coverage weight of zero;
three purchase days do not imply confidence >=0.5, verified identity or complete
history. The synthetic three-day E2E explicitly approves 0.3 only inside the test;
this is NOT a recommended or configured production threshold.

Runs freeze generator, parameter version and full parameters. Resume supersedes an
old-policy checkpoint before further scanning/evaluation. Reuse and publication
check the selected algorithm. Old SHADOW rows/history remain SHADOW; same-policy
reruns reuse the run. Re-evaluation of an existing episode retains identity and
revises evidence rather than duplicating an open episode.

Independent release now additionally requires an explicitly drafted and approved
`algorithm_policy: aviator-churn-pilot/1` in producer policy settings. Old approved
settings cannot authorize this algorithm. Existing release HTTP endpoints reject
old policy settings (503) and old release metadata (410); publication listing
filters incompatible releases. All prior source/manifest/identity/manager guards
remain. Raw coverage/freshness remain UNKNOWN and automation_allowed=false.
An approved synthetic release is a separate stream, never an update of raw signals.

## Verification

Use the existing opt-in, explicitly named local PostgreSQL/MariaDB test stand.
Tests never use application DATABASE_URL. See test_churn_pilot.py for 44/45/46,
positive day aggregation, returns, store/category exclusions and checkpoint guards.
The parameterized test_churn_automation_e2e.py verifies both the lifetime-cap case
and a six-client/three-day pilot through the native worker, independently signed
fixture facts, release HTTP API, and concurrent PHP consumers. The pilot creates
five tasks globally, blocks the sixth, preserves replay and sets due_at to +3 days.
The admin bootstrap regression retains external_id A != client_id B rejection.

## Future controlled adoption (not performed)

No PostgreSQL migration is needed for this policy: existing JSON settings carry
its version. First review verified recovery, actual deployed commits/schema,
source registry/completeness/currentness, full-history identity reviews and manager
bindings. Independently approve confidence, priority, lifetime budget, source list,
TTL/expiry, stop and retry rules. Retain disabled SHADOW defaults until separate
permission. Explicitly draft/approve the producer algorithm policy and approve
this generator and exact scope on the consumer; old approvals are insufficient.
Calltrack also needs its new daily-budget SQL after its existing budget migration.
Do not reset cursors or promote old SHADOW payloads. First process a new independently
approved fresh release after dry-run review. No production work is authorized here.
