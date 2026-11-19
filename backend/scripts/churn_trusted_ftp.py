"""Host-only adapter: authenticated local-import statement and fresh Calltrack receipts."""
import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener

MAX_PAGE = 512 * 1024
MAX_ENVELOPE = 4 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_private(path, maximum=16384):
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError("PRIVATE_ABSOLUTE_PATH_REQUIRED")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError("UNTRUSTED_PRIVATE_FILE")
        data = handle.read(maximum + 1)
        if not data or len(data) > maximum:
            raise ValueError("INVALID_PRIVATE_FILE_SIZE")
        return data


def receipt_pages(url, token, *, budget=30):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not parsed.path.endswith("/api/analytics_verified_export.php")
            or len(token) < 32 or not token.isascii() or any(char.isspace() for char in token)):
        raise ValueError("INVALID_CALLTRACK_EXPORT_CONFIG")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + budget
    cursor = ""
    for _ in range(100):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("CALLTRACK_EXPORT_TIMEOUT")
        request = Request(url + "?" + urlencode({"after": cursor}),
                          headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        with opener.open(request, timeout=min(5, remaining)) as response:
            raw = response.read(MAX_PAGE + 1)
        if len(raw) > MAX_PAGE or time.monotonic() > deadline:
            raise ValueError("CALLTRACK_EXPORT_LIMIT")
        page = json.loads(raw)
        if (not isinstance(page, dict) or page.get("version") != 1 or page.get("automation_allowed") is not False
                or not isinstance(page.get("receipts"), list) or len(page["receipts"]) > 5):
            raise ValueError("CALLTRACK_EXPORT_SHAPE")
        yield from page["receipts"]
        next_cursor = page.get("next_after")
        if next_cursor is None:
            return
        if not isinstance(next_cursor, str) or not re.fullmatch(r"[a-f0-9]{64}", next_cursor) or next_cursor <= cursor:
            raise ValueError("CALLTRACK_EXPORT_CURSOR")
        cursor = next_cursor
    raise ValueError("CALLTRACK_EXPORT_PAGE_LIMIT")


def collect_bindings(context, envelopes, *, now):
    targets = {}
    duplicates = set()
    for target in context["targets"]:
        key = target["client_id"]
        if key in targets:
            duplicates.add(key)
        targets[key] = target
    for key in duplicates:
        del targets[key]
    receipts, groups, assignments = {}, {}, {}
    cache_revision = None
    for envelope in envelopes:
        if not isinstance(envelope, dict) or envelope.get("algorithm") != "Ed25519":
            continue
        raw = envelope.get("payload_json")
        if not isinstance(raw, str) or len(raw.encode()) > 65536:
            continue
        review = json.loads(raw)
        if not isinstance(review, dict):
            continue
        key = review.get("client_id")
        if not isinstance(key, str) or key not in targets:
            continue
        target = targets[key]
        if (review.get("issuer") != "calltrack" or review.get("audience") != "sales-journal"
                or review.get("status") != target["status"] or review.get("scope") != context["scope"]
                or review.get("client_key") != target["client_key"] or review.get("manager_id") != target["manager_id"]
                or review.get("binding_revision") != target["revision"] or review.get("last_purchase") != target["last_purchase"]):
            continue
        try:
            issued, until = (datetime.fromisoformat(review[name]) for name in ("issued_at", "valid_until"))
            if not issued <= now < until or until-issued > timedelta(hours=1):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        revision, group = review.get("cache_revision"), review.get("group_hash")
        if (not isinstance(revision, str) or not revision or len(revision) > 128
                or not isinstance(group, str) or not re.fullmatch(r"[a-f0-9]{64}", group)):
            continue
        if cache_revision is not None and cache_revision != revision:
            raise ValueError("CALLTRACK_CACHE_CHANGED_DURING_EXPORT")
        cache_revision = revision
        if key in receipts and receipts[key] != envelope:
            raise ValueError("CALLTRACK_DUPLICATE_RECEIPT")
        receipts[key] = envelope
        groups[key] = {"client_id": key, "group_hash": group}
        assignments[key] = {name: target[name] for name in ("client_id", "manager_id", "revision", "subject_hash")}
        if len(receipts) == len(targets):
            break
    if not receipts:
        raise ValueError("NO_CURRENT_CALLTRACK_RECEIPTS")
    return receipts, [groups[k] for k in sorted(groups)], [assignments[k] for k in sorted(assignments)], cache_revision


def pack(context, envelopes, *, sign_ed25519, key_id, hmac_key, now):
    if context.get("source_trust_mode") != "TRUSTED_IMPORT":
        raise ValueError("TRUSTED_CONTEXT_REQUIRED")
    statement = dict(context["statement"])
    if (statement.get("complete") is not False or not isinstance(statement.get("as_of"), str)
            or statement.get("declared_complete_through") != statement["as_of"]
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", key_id) or len(hmac_key) < 32):
        raise ValueError("INVALID_TRUSTED_STATEMENT")
    receipts, groups, assignments, cache_revision = collect_bindings(context, envelopes, now=now)
    now = now.replace(microsecond=0)
    max_age = context["max_age"]
    if type(max_age) is not int or max_age < 1:
        raise ValueError("INVALID_SIGNING_AGE")
    valid_until = min(now + timedelta(seconds=min(900, max_age)), *(
        datetime.fromisoformat(json.loads(receipt["payload_json"])["valid_until"]) for receipt in receipts.values()
    ))
    if valid_until <= now:
        raise ValueError("NO_CURRENT_CALLTRACK_RECEIPTS")
    statement.update(audience="sales-journal+calltrack", clients_current=True, clients_revision=cache_revision,
                     clients_cache_revision=cache_revision, reviewed_count=len(receipts), review_groups=groups,
                     assignments=assignments, issued_at=now.isoformat(), valid_until=valid_until.isoformat(),
                     source_revisions={"sales_ftp": str(statement["source_revision"]), "clientsvr": cache_revision})
    raw = canonical(statement).encode()
    if len(raw) > 65536:
        raise ValueError("TRUSTED_STATEMENT_TOO_LARGE")
    signature = sign_ed25519(raw)
    if len(signature) != 64:
        raise ValueError("INVALID_ED25519_SIGNATURE")
    bundle = {"upstream_manifest": dict(algorithm="Ed25519", key_id=key_id, payload_json=raw.decode(),
                  signature=base64.b64encode(signature).decode()), "binding_receipts": receipts}
    proof = dict(statement, receiver_evidence_hash=digest(bundle))
    result = {"manifest": {"payload": proof, "signature": hmac.new(hmac_key, canonical(proof).encode(), sha256).hexdigest()},
              "receiver_evidence": bundle}
    if len(canonical(result).encode()) > MAX_ENVELOPE:
        raise ValueError("TRUSTED_ENVELOPE_TOO_LARGE")
    return result


def openssl_sign(raw, key, *, directory):
    # Ed25519 is one-shot and OpenSSL needs the exact size, so pass a private file.
    with tempfile.TemporaryDirectory(dir=directory) as work:
        key_path, data_path = Path(work, "key.pem"), Path(work, "payload")
        key_path.write_bytes(key)
        data_path.write_bytes(raw)
        return subprocess.run(
            ["/usr/bin/openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(key_path), "-in", str(data_path)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin", "LC_ALL": "C"}, timeout=5,
        ).stdout


def build(context, config_path):
    config = json.loads(read_private(config_path))
    if set(config) != {"export_url", "export_token_file", "ed25519_key_file", "key_id", "hmac_key_file"}:
        raise ValueError("INVALID_TRUSTED_HOST_CONFIG")
    token = read_private(config["export_token_file"]).decode().strip()
    key = read_private(config["ed25519_key_file"])
    hmac_key = read_private(config["hmac_key_file"]).strip()
    # Collect over HTTPS first, then recheck freshness against the actual signing time.
    envelopes = []
    targets = {x["client_id"] for x in context["targets"]}
    if not targets:
        raise ValueError("NO_TRUSTED_BINDING_TARGETS")
    for envelope in receipt_pages(config["export_url"], token):
        if not isinstance(envelope, dict) or not isinstance(envelope.get("payload_json"), str):
            continue
        row = json.loads(envelope["payload_json"])
        if isinstance(row, dict) and row.get("client_id") in targets:
            envelopes.append(envelope)
            if len(envelopes) > 200:
                raise ValueError("EXCESS_TRUSTED_RECEIPTS")
    return pack(context, envelopes, sign_ed25519=lambda raw: openssl_sign(
        raw, key, directory="/run/sales-churn-permanent"), key_id=config["key_id"],
        hmac_key=hmac_key, now=datetime.now(timezone.utc))
