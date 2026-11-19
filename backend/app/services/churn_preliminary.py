"""Allow only fresh, context-bound Calltrack preliminary reviews.

This module parses and binds the signed bytes. Calltrack verifies the Ed25519
signature and rechecks the live directory and assignment before task creation.
"""

import json
import re
from datetime import datetime, timedelta

PRELIMINARY = "PRELIMINARY_MATCH"
MATCHING_POLICY = "preliminary-customer-group/2"
MATCHING_METHOD = "EXACT_NAME_SINGLE_MANAGER"


def preliminary_matching(envelope, *, client_key, client_id, manager_id, revision,
                         last_purchase, scope, now):
    if not isinstance(envelope, dict) or envelope.get("algorithm") != "Ed25519":
        return None
    raw = envelope.get("payload_json")
    if not isinstance(raw, str) or len(raw) > 65536 or not envelope.get("key_id") or not envelope.get("signature"):
        return None
    try:
        review = json.loads(raw)
        if not isinstance(review, dict):
            return None
        issued = datetime.fromisoformat(review["issued_at"])
        until = datetime.fromisoformat(review["valid_until"])
        if not issued <= now < until or until - issued > timedelta(hours=1):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    if type(manager_id) is not int or manager_id <= 0 or type(revision) is not int or revision < 1:
        return None
    if type(review.get("version")) is not int or type(review.get("manager_id")) is not int or type(review.get("binding_revision")) is not int:
        return None
    expected = dict(version=1, issuer="calltrack", audience="sales-journal",
                    status=PRELIMINARY, client_key=client_key, client_id=client_id,
                    manager_id=manager_id, binding_revision=revision,
                    last_purchase=last_purchase, scope=scope)
    if any(review.get(key) != value for key, value in expected.items()):
        return None
    matching = review.get("matching")
    if not isinstance(matching, dict):
        return None
    if (matching.get("policy") != MATCHING_POLICY or matching.get("method") != MATCHING_METHOD
            or matching.get("status") != PRELIMINARY or matching.get("manual_verified") is not False
            or matching.get("client_id") != client_id or type(matching.get("manager_id")) is not int
            or matching["manager_id"] != manager_id
            or not re.fullmatch(r"[a-f0-9]{64}", str(matching.get("group_key", "")))):
        return None
    members = matching.get("member_ids")
    if not isinstance(members, list) or not 1 <= len(members) <= 500:
        return None
    if any(not isinstance(member, str) or not member for member in members):
        return None
    if client_id not in members or len(set(members)) != len(members):
        return None
    return matching
