"""Subtract certificate payments from sale totals.

Existing fingerprints intentionally remain unchanged. The importer checks both
the new net-total fingerprint and the historical gross-total fingerprint.
"""
from alembic import op

revision="20260921_05";down_revision="20260921_04";branch_labels=None;depends_on=None

def upgrade():
 op.execute("UPDATE sales SET total_amount = total_amount - certificate_amount WHERE certificate_amount > 0")

def downgrade():
 op.execute("UPDATE sales SET total_amount = total_amount + certificate_amount WHERE certificate_amount > 0")
