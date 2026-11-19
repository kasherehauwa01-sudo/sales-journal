"""Wire contract shared with Calltrack. Trusted imports are never marked complete."""

INDEPENDENT = "INDEPENDENT"
TRUSTED_IMPORT = "TRUSTED_IMPORT"


def valid_approval(value):
    return (
        isinstance(value, str) and bool(value.strip())
        and value.strip().upper() not in {"UNKNOWN", "UNSET", "NULL", "NONE"}
    )


def policy_mode(config):
    mode = config.get("source_trust_mode", INDEPENDENT)
    if mode not in (INDEPENDENT, TRUSTED_IMPORT):
        raise ValueError("INVALID_SOURCE_TRUST_MODE")
    if mode == TRUSTED_IMPORT and not valid_approval(config.get("import_trust_approval_id")):
        raise ValueError("IMPORT_TRUST_NOT_APPROVED")
    return mode


def source_decision(mode):
    if mode == TRUSTED_IMPORT:
        return dict(method="OWNER_TRUSTED_IMPORT_SIGNED_V1", coverage_status="DECLARED",
                    business_freshness="DECLARED_CURRENT")
    if mode != INDEPENDENT:
        raise ValueError("INVALID_SOURCE_TRUST_MODE")
    return dict(method="INDEPENDENT_SIGNED_UPSTREAM_MANIFEST_V1", coverage_status="CONFIRMED",
                business_freshness="CURRENT")


def accepts_statement(config, payload, sales_day):
    mode = policy_mode(config)
    if payload.get("source_trust_mode", INDEPENDENT) != mode:
        return False
    if mode == INDEPENDENT:
        return payload.get("complete") is True
    return (
        isinstance(sales_day, str)
        and payload.get("complete") is False
        and payload.get("declared_complete_through") == sales_day
        and payload.get("import_trust_approval_id") == config["import_trust_approval_id"]
    )


def schedule_agrees(schedule, producer):
    mode = policy_mode(schedule)
    if mode != policy_mode(producer):
        return False
    return mode == INDEPENDENT or schedule.get("import_trust_approval_id") == producer.get("import_trust_approval_id")
