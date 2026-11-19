from datetime import datetime, timezone
from hashlib import sha256
import base64
import hmac
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.churn_trusted_ftp import canonical, collect_bindings, digest, openssl_sign, pack, receipt_pages


class TrustedHostTest(unittest.TestCase):
    now = datetime(2026, 11, 19, 7, 30, tzinfo=timezone.utc)
    scope = {"department": "Авиаторов"}
    target = dict(client_key="test", client_id="id-test", manager_id=6, revision=1,
                  last_purchase="2026-09-01", status="PRELIMINARY_MATCH", subject_hash="a"*64)
    context = dict(source_trust_mode="TRUSTED_IMPORT", scope=scope, targets=[target], max_age=900,
                   statement=dict(complete=False, as_of="2026-11-18", declared_complete_through="2026-11-18",
                                  source_revision=42, candidate_count=1))

    def receipt(self, **override):
        payload = dict(issuer="calltrack", audience="sales-journal", client_key="test", client_id="id-test",
                       scope=self.scope, manager_id=6, binding_revision=1, last_purchase="2026-09-01",
                       status="PRELIMINARY_MATCH", issued_at="2026-11-19T07:00:00+00:00",
                       valid_until="2026-11-19T07:33:00+00:00", group_hash="b"*64, cache_revision="cache-test")
        return dict(algorithm="Ed25519", key_id="ct", signature="fixture",
                    payload_json=canonical(payload | override))

    def test_changed_manager_and_stale_receipt_do_not_enter_package(self):
        for receipt in (self.receipt(manager_id=3), self.receipt(valid_until=self.now.isoformat()),
                        self.receipt(binding_revision=2)):
            with self.assertRaisesRegex(ValueError, "NO_CURRENT"):
                collect_bindings(self.context, [receipt], now=self.now)

    def test_non_https_or_wrong_path_are_rejected_before_network(self):
        for url in ("http://example.test/api/analytics_verified_export.php", "https://example.test/other",
                    "https://user@example.test/api/analytics_verified_export.php"):
            with self.assertRaisesRegex(ValueError, "INVALID_CALLTRACK_EXPORT_CONFIG"):
                list(receipt_pages(url, "t"*32))

    def test_duplicate_target_cannot_admit_ambiguous_customer(self):
        context = self.context | {"targets": [self.target, self.target | {"client_key": "alias"}]}
        with self.assertRaisesRegex(ValueError, "NO_CURRENT"):
            collect_bindings(context, [self.receipt()], now=self.now)

    @unittest.skipUnless(shutil.which("openssl"), "requires OpenSSL")
    def test_real_signatures_bind_import_and_receipt_without_completeness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = subprocess.check_output(["openssl", "genpkey", "-algorithm", "ED25519"])
            pub = subprocess.check_output(["openssl", "pkey", "-pubout"], input=key)
            hmac_key = b"h"*32
            result = pack(self.context, [self.receipt()], key_id="ftp-test", hmac_key=hmac_key,
                          now=self.now, sign_ed25519=lambda raw: openssl_sign(raw, key, directory=directory))
            manifest, bundle = result["manifest"], result["receiver_evidence"]
            proof = manifest["payload"]
            envelope = bundle["upstream_manifest"]
            self.assertIs(proof["complete"], False)
            self.assertEqual(proof["valid_until"], "2026-11-19T07:33:00+00:00")
            self.assertEqual(proof["receiver_evidence_hash"], digest(bundle))
            self.assertEqual(manifest["signature"], hmac.new(hmac_key, canonical(proof).encode(), sha256).hexdigest())
            for name,data in (("pub", pub), ("payload", envelope["payload_json"].encode()),
                              ("signature", base64.b64decode(envelope["signature"]))):
                (root/name).write_bytes(data)
            cmd = ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin",
                   "-inkey", str(root/"pub"), "-in", str(root/"payload"), "-sigfile", str(root/"signature")]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (root/"payload").write_bytes(envelope["payload_json"].encode() + b" ")
            self.assertNotEqual(subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode, 0)
