import unittest

from app.services.churn_source_trust import (
    INDEPENDENT, TRUSTED_IMPORT, accepts_statement, policy_mode, schedule_agrees, source_decision,
)


class ChurnSourceTrustTest(unittest.TestCase):
    day = "2026-11-18"
    config = {"source_trust_mode": TRUSTED_IMPORT, "import_trust_approval_id": "ftp-policy-1"}

    def statement(self, **overrides):
        return dict(self.config, complete=False, declared_complete_through=self.day) | overrides

    def test_trusted_import_declares_but_does_not_confirm(self):
        self.assertTrue(accepts_statement(self.config, self.statement(), self.day))
        self.assertEqual(source_decision(TRUSTED_IMPORT), {
            "method": "OWNER_TRUSTED_IMPORT_SIGNED_V1",
            "coverage_status": "DECLARED",
            "business_freshness": "DECLARED_CURRENT",
        })

    def test_rejects_claim_of_complete_import(self):
        self.assertFalse(accepts_statement(self.config, self.statement(complete=True), self.day))
        self.assertFalse(accepts_statement(self.config, self.statement(complete=0), self.day))

    def test_rejects_mismatched_day_or_approval(self):
        self.assertFalse(accepts_statement(self.config, self.statement(declared_complete_through="2026-11-17"), self.day))
        self.assertFalse(accepts_statement(self.config, self.statement(import_trust_approval_id="other"), self.day))

    def test_existing_modes_cannot_be_mixed(self):
        self.assertEqual(policy_mode({}), INDEPENDENT)
        self.assertTrue(accepts_statement({}, {"complete": True}, self.day))
        self.assertFalse(accepts_statement({}, self.statement(), self.day))
        self.assertFalse(accepts_statement(self.config, {"complete": True}, self.day))
        self.assertTrue(schedule_agrees({}, {}))
        self.assertTrue(schedule_agrees(self.config, self.config))
        self.assertFalse(schedule_agrees(self.config, {}))
        self.assertFalse(schedule_agrees(self.config, self.config | {"import_trust_approval_id": "other"}))

    def test_rejects_invalid_policy_defaults(self):
        for invalid in ("", "UNKNOWN", "none", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                policy_mode(self.config | {"import_trust_approval_id": invalid})
        with self.assertRaises(ValueError):
            policy_mode({"source_trust_mode": "unknown"})
