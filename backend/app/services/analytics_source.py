"""Conservative SHADOW date decisions; no inferred completeness or source registry."""
from dataclasses import asdict, dataclass
from datetime import date, datetime
from hashlib import sha256
import json

from sqlalchemy import select
from app.models import ImportBatch
from app.importer.source_period import METHOD
from app.services.inactivity import json_value

POLICY_VERSION = "safe-as-of/1"
EVIDENCE_LIMIT = 1000


@dataclass(frozen=True)
class ImportEvidence:
    id: int
    filename: str
    status: str
    error_rows: int = 0
    imported_at: datetime | None = None
    declared_start: date | None = None
    declared_end: date | None = None
    observed_start: date | None = None
    observed_end: date | None = None
    method: str | None = None


@dataclass(frozen=True)
class SourceDecision:
    requested_as_of: date
    last_closed_business_day: date
    declared_data_through: date | None
    observed_data_through: date | None
    accepted_data_through: date | None
    effective_as_of: date | None
    coverage_status: str
    coverage_method: str | None
    business_freshness: str
    reason_codes: tuple[str, ...]
    source_revision: int
    evidence: tuple[ImportEvidence, ...]
    evidence_fingerprint: str
    timezone: str
    shadow_allowed: bool
    automation_allowed: bool = False
    policy_version: str = POLICY_VERSION
    source_scope: str = "ALL_RECORDED_IMPORTS_CONSERVATIVE_INTERSECTION"
    source_scope_limitation: str = "Required upstream sources are unknown; no completeness guarantee."

    def to_json(self):
        return json_value(asdict(self))


def resolve_source(requested_as_of: date, evidence: tuple[ImportEvidence, ...], *,
                   last_closed_business_day: date, source_revision: int, timezone: str) -> SourceDecision:
    """All recorded imports constrain the date; never silently pick the newest store.

    No reliable upstream source identity/required-source set exists today. Thus
    legacy/unknown/failed imports block, and overlapping declared periods use
    their intersection. This intentionally favours safety over availability.
    """
    evidence = tuple(sorted(evidence, key=lambda e: e.id))
    digest = sha256(json.dumps(json_value([asdict(e) for e in evidence]), sort_keys=True).encode()).hexdigest()
    reasons = []
    unhealthy = False
    declared = min((e.declared_end for e in evidence if e.declared_end), default=None)
    observed = max((e.observed_end for e in evidence if e.observed_end), default=None)
    if not evidence:
        reasons.append("NO_IMPORT_EVIDENCE")
    if len(evidence) > EVIDENCE_LIMIT:
        reasons.append("IMPORT_EVIDENCE_LIMIT_EXCEEDED")
    for e in evidence:
        if e.status != "completed" or e.error_rows or not e.imported_at:
            unhealthy = True
            reasons.append("IMPORT_PENDING_OR_FAILED_OR_ERRORS")
        if e.method in {"INVALID_REPORT_PERIOD", "METADATA_EXTRACTION_FAILED"}:
            unhealthy = True
            reasons.append("INVALID_DECLARED_PERIOD")
        elif e.method != METHOD or not e.declared_start or not e.declared_end:
            reasons.append("DECLARED_PERIOD_MISSING")
        if e.declared_start and e.declared_end:
            if e.declared_start > e.declared_end:
                unhealthy = True
                reasons.append("INVALID_DECLARED_PERIOD")
            if ((e.observed_start and e.observed_start < e.declared_start)
                    or (e.observed_end and e.observed_end > e.declared_end)):
                unhealthy = True
                reasons.append("OBSERVED_OUTSIDE_DECLARED_PERIOD")
    accepted = effective = None
    if not reasons:
        start = max(e.declared_start for e in evidence)
        if start > declared:
            reasons.append("NO_COMMON_DECLARED_PERIOD")
        elif min(requested_as_of, last_closed_business_day, declared) < start:
            reasons.append("REQUESTED_DATE_OUTSIDE_DECLARED_PERIOD")
        else:
            accepted = declared
            effective = min(requested_as_of, accepted, last_closed_business_day)
    allowed = effective is not None
    coverage = "UNHEALTHY" if unhealthy else "DECLARED" if allowed else "UNKNOWN"
    business = ("UNHEALTHY" if unhealthy else "UNKNOWN" if not accepted else
                "STALE" if accepted < last_closed_business_day else "CURRENT")
    if allowed:
        if requested_as_of > accepted:
            reasons.append("REQUESTED_DATE_AFTER_AVAILABLE_DATA")
        if requested_as_of > last_closed_business_day:
            reasons.append("REQUESTED_DATE_NOT_CLOSED")
        if not reasons:
            reasons.append("REQUESTED_DATE_ACCEPTED")
    return SourceDecision(requested_as_of, last_closed_business_day, declared, observed,
        accepted, effective, coverage, METHOD if allowed else None, business,
        tuple(dict.fromkeys(reasons)), source_revision, evidence, digest, timezone, allowed)


async def load_source_evidence(db):
    # Explicit columns avoid ImportBatch.errors select-in hydration.
    rows = (await db.execute(select(ImportBatch.id, ImportBatch.filename, ImportBatch.status,
        ImportBatch.error_rows, ImportBatch.finished_at, ImportBatch.declared_period_start,
        ImportBatch.declared_period_end, ImportBatch.period_start, ImportBatch.period_end,
        ImportBatch.source_period_method).order_by(ImportBatch.id).limit(EVIDENCE_LIMIT + 1))).all()
    return tuple(ImportEvidence(*row) for row in rows)


STORED_SALES_POLICY_VERSION = "stored-sales-shadow/1"


def resolve_stored_sales_source(requested_as_of: date, *, last_closed_business_day: date,
        source_revision: int, timezone: str, has_sales: bool, upper_sale_id: int,
        imports_pending: bool = False) -> SourceDecision:
    """A snapshot of stored rows, NEVER a completeness/freshness attestation.

    has_sales refers to dated, named rows in the verified business scope at the
    cutoff. Neither import report dates nor MAX(sale_date) establish coverage.
    Fingerprint excludes the moving wall clock so the same frozen cutoff can
    resume tomorrow, but includes the PK watermark as a secondary change guard.
    """
    reasons = ["STORED_SALES_COMPLETENESS_UNKNOWN"]
    if requested_as_of > last_closed_business_day:
        reasons.append("REQUESTED_DATE_NOT_CLOSED")
    if imports_pending:
        reasons.append("IMPORT_PENDING")
    if not has_sales or upper_sale_id <= 0:
        reasons.append("NO_SCOPED_STORED_SALES")
    allowed = has_sales and upper_sale_id > 0 and not imports_pending
    digest = sha256(json.dumps(dict(policy=STORED_SALES_POLICY_VERSION,
        source_revision=source_revision, upper_sale_id=upper_sale_id), sort_keys=True).encode()).hexdigest()
    return SourceDecision(requested_as_of=requested_as_of,
        last_closed_business_day=last_closed_business_day,
        declared_data_through=None, observed_data_through=None,
        accepted_data_through=None,
        effective_as_of=min(requested_as_of, last_closed_business_day) if allowed else None,
        coverage_status="UNKNOWN", coverage_method="STORED_SALES",
        business_freshness="UNKNOWN", reason_codes=tuple(reasons),
        source_revision=source_revision, evidence=(), evidence_fingerprint=digest,
        timezone=timezone, shadow_allowed=allowed, automation_allowed=False,
        policy_version=STORED_SALES_POLICY_VERSION, source_scope="SCOPED_STORED_POSTGRESQL_SALES",
        source_scope_limitation="Stored rows only; neither completeness nor upstream freshness is confirmed.")


def stored_sales_contract_valid(metadata):
    """Reject any stored snapshot claiming authority not provided by this policy."""
    source = metadata.get("source_decision") or {}
    return (metadata.get("mode") == "SHADOW"
        and metadata.get("automation_allowed") is False
        and metadata.get("coverage_status") == "UNKNOWN"
        and (metadata.get("freshness") or {}).get("status") == "UNKNOWN"
        and metadata.get("complete_through") is None
        and source.get("policy_version") == STORED_SALES_POLICY_VERSION
        and source.get("coverage_status") == source.get("business_freshness") == "UNKNOWN"
        and source.get("automation_allowed") is False
        and source.get("accepted_data_through") is None)
