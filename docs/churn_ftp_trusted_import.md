# Churn: production FTP as a declared source

This draft is not a deployment or an instruction to enable task creation. It
implements the producer policy and the local FTP trigger. The host provider and
Calltrack end-to-end tests must still be supplied.

Production uses only the existing 1C FTP data files. No change to 1C, marker
file, independent source-completeness declaration or daily manual confirmation
is part of this mode.

* New permanent jobs use the finished FTP cycle and successful import receipts
  for the last closed Moscow day. A later empty poll does not erase that
  evidence. A running cycle for that day defers the job.
* Both permanent and producer policies must select TRUSTED_IMPORT and carry the
  same one-time policy identifier. The existing INDEPENDENT mode is unchanged.
  Ordinary producer release CLI cannot publish TRUSTED_IMPORT directly.
* The signed wire statement must say complete=false and carry the requested day
  as declared_complete_through. Calltrack's existing wire contract identifies
  such runs as DECLARED, never CONFIRMED. Authentication of the bytes does not
  attest completeness of the business data.
* Current source revision, no pending import, client/manager binding, task
  budget, deduplication and receiver acknowledgments are still checked.

## Remaining deployment work

Implement the host evidence provider: build a bounded description of the
committed FTP calculation, obtain genuine Calltrack binding receipts, and sign
the source description with an operator-managed trusted-import key. No
production private key is stored in this repository. Test the actual PHP and
MariaDB Calltrack receiver with the FirstVDS reopen/delete variants, including
replay and a one-task budget. Deploy with both policies disabled first.

## Preliminary matching

The permanent trusted-import path can use a current Calltrack-signed
PRELIMINARY_MATCH for the existing exact-name/single-manager policy. Sales
Journal binds it to the current subject, manager, binding revision, scope and
last purchase. It never upgrades a binding to VERIFIED. Calltrack still
verifies the cryptographic signature and recalculates the matching group
before creating a task. Existing independent mode still requires VERIFIED.

This does not start the host provider, provision its keys, replay a real
signal or test production MariaDB. The historical pilot file is used only as a
wire-format reference, never as a fresh receipt.
