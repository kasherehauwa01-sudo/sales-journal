from datetime import datetime, timezone
import json
import unittest

from app.services.churn_preliminary import MATCHING_METHOD, MATCHING_POLICY, PRELIMINARY, preliminary_matching


class PreliminaryReceiptTest(unittest.TestCase):
    def setUp(self):
        self.context = dict(client_key="test customer", client_id="client-test",
                            manager_id=6, revision=2, last_purchase="2026-09-05",
                            scope={"department": "Авиаторов"},
                            now=datetime(2026, 11, 19, 7, 30, tzinfo=timezone.utc))
        self.metadata = dict(policy=MATCHING_POLICY, method=MATCHING_METHOD,
                             status=PRELIMINARY, manual_verified=False, client_id="client-test",
                             manager_id=6, group_key="a"*64, member_ids=["client-test"])
        self.payload = dict(version=1, status=PRELIMINARY, issuer="calltrack", audience="sales-journal",
                            client_key="test customer", client_id="client-test", manager_id=6,
                            binding_revision=2, last_purchase="2026-09-05",
                            scope=self.context["scope"], issued_at="2026-11-19T07:00:00+00:00",
                            valid_until="2026-11-19T08:00:00+00:00", matching=self.metadata)

    def check(self, **changes):
        receipt = dict(algorithm="Ed25519", signature="structure-test", key_id="test",
                       payload_json=json.dumps(self.payload | changes))
        return preliminary_matching(receipt, **self.context)

    def test_fresh_unchanged_review_is_preserved_without_promotion(self):
        self.assertEqual(self.check(), self.metadata)
        self.assertIs(self.check()["manual_verified"], False)
        self.assertEqual(self.check()["status"], PRELIMINARY)

    def test_manager_subject_revision_last_purchase_and_scope_are_bound(self):
        for key, value in {"manager_id":3, "client_key":"different", "client_id":"other-id",
                           "binding_revision":1, "last_purchase":"2026-09-20", "scope":{},
                           "status":"VERIFIED", "issuer":"unexpected"}.items():
            with self.subTest(key=key):
                self.assertIsNone(self.check(**{key:value}))

    def test_expired_future_and_overlong_receipts_are_rejected(self):
        self.assertIsNone(self.check(valid_until="2026-11-19T07:30:00+00:00"))
        self.assertIsNone(self.check(issued_at="2026-11-19T07:31:00+00:00"))
        self.assertIsNone(self.check(valid_until="2026-11-19T08:01:00+00:00"))
        self.assertIsNone(self.check(issued_at="2026-11-19T07:00:00"))

    def test_matching_cannot_be_changed_or_claim_manual_verification(self):
        for key,value in {"policy":"other-policy", "manager_id":3, "manual_verified":True,
                          "member_ids":["other"], "group_key":"bad", "method":"PHONE_MATCH"}.items():
            with self.subTest(key=key):
                self.assertIsNone(self.check(matching=self.metadata | {key:value}))

    def test_signed_receipt_is_required(self):
        for receipt in ({}, None, {"algorithm":"Ed25519","payload_json":"{}"}):
            self.assertIsNone(preliminary_matching(receipt, **self.context))
