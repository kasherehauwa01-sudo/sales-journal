"""Bind receiver evidence to the existing HMAC release.

Ed25519 verification remains mandatory in Calltrack's analyticsVerifiedApproval.
This adapter cannot sign facts, assert completeness in trusted-import mode,
or promote preliminary identities.
"""
import json
from datetime import datetime, timezone
from hashlib import sha256
from app.services.analytics_automation import digest
from app.services.churn_preliminary import PRELIMINARY, MATCHING_POLICY, preliminary_matching
from app.services.churn_source_trust import TRUSTED_IMPORT, accepts_statement, policy_mode


def attach(event, bundle, *, expected_hash, policy_revision, config, producer_proof=None):
    if digest(bundle) != expected_hash or set(bundle) != {'upstream_manifest', 'binding_receipts'}:
        raise ValueError('RECEIVER_EVIDENCE_NOT_BOUND')
    source_envelope = bundle['upstream_manifest']
    receipts = bundle['binding_receipts']
    if not isinstance(receipts, dict) or len(receipts) > 1000:
        raise ValueError('RECEIVER_EVIDENCE_INVALID')
    client = event['subject']['key']
    receipt = receipts.get(client)
    source = json.loads(source_envelope['payload_json'])
    review = json.loads(receipt['payload_json']) if isinstance(receipt, dict) else {}
    if producer_proof is not None and any(source.get(k) != producer_proof.get(k) for k in (
            'source_run_id', 'source_revision', 'as_of', 'complete_through', 'fact_count', 'facts_hash',
            'clients_current', 'clients_revision', 'source_revisions')):
        raise ValueError('RECEIVER_PROOF_CONTEXT_MISMATCH')
    mode = policy_mode(config)
    if (not accepts_statement(config, source, event['as_of'])
            or (mode == TRUSTED_IMPORT and (producer_proof is None
                or not accepts_statement(config, producer_proof, event['as_of'])))):
        raise ValueError('RECEIVER_SOURCE_TRUST_MISMATCH')
    matching = event.get('matching')
    expected_status = 'VERIFIED'
    if 'matching' in event:
        expected_status = PRELIMINARY
        if (mode != TRUSTED_IMPORT or source.get('matching_policy') != MATCHING_POLICY
                or producer_proof.get('matching_policy') != MATCHING_POLICY
                or not isinstance(matching, dict) or not isinstance(review.get('client_key'), str)
                or matching != preliminary_matching(receipt, client_key=review['client_key'],
                    client_id=client, manager_id=event['assignment']['manager_id'],
                    revision=event['assignment']['binding_revision'],
                    last_purchase=event['last_purchase'], scope=event['run']['scope'],
                    now=datetime.now(timezone.utc))):
            raise ValueError('RECEIVER_PRELIMINARY_MISMATCH')
    if (source.get('source_run_id') != event['provenance']['shadow_run_id']
            or source.get('source_revision') != event['run']['source_revision']
            or source.get('as_of') != event['as_of']
            or review.get('status') != expected_status or review.get('client_id') != client
            or review.get('manager_id') != event['assignment']['manager_id']
            or review.get('binding_revision') != event['assignment']['binding_revision']
            or review.get('last_purchase') != event['last_purchase']):
        raise ValueError('RECEIVER_PROOF_CONTEXT_MISMATCH')
    # JSON roundtrip creates only one bounded event, not a copy of the fact set.
    result = json.loads(json.dumps(event))
    proof_hash = sha256(source_envelope['payload_json'].encode()).hexdigest()
    result['run']['source_decision']['proof_hash'] = proof_hash
    result['provenance']['proof_hash'] = proof_hash
    result['upstream_manifest'] = source_envelope
    result['binding_receipt'] = receipt
    result['permanent_policy'] = dict(version='churn-permanent/1', revision=config['receiver_policy_revision'],
        producer_revision=policy_revision, daily_limit=config['daily_limit'], cooldown_days=config['cooldown_days'])
    return result
