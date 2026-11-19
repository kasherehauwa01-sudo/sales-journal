"""Exercise the production predicate with a disposable SQL database."""

from contextlib import closing
import sqlite3
import unittest

from app.services.churn_ftp_trigger import FTP_CYCLE_READY


class ChurnFtpTriggerTest(unittest.TestCase):
    day = "2026-11-18"

    def setUp(self):
        self.db = self.enterContext(closing(sqlite3.connect(":memory:")))
        self.db.executescript("""
            CREATE TABLE import_control_cycles (
                id TEXT PRIMARY KEY, sales_date DATE, status TEXT,
                finished_at TEXT, source_confirmed BOOLEAN
            );
            CREATE TABLE import_control_receipts (
                cycle_id TEXT, import_id INTEGER, period_end DATE, status TEXT
            );
            CREATE TABLE import_batches (id INTEGER PRIMARY KEY, status TEXT, error_rows INTEGER);
        """)

    def cycle(self, key, state, day=None):
        self.db.execute(
            "INSERT INTO import_control_cycles VALUES (?, ?, ?, ?, FALSE)",
            (key, day or self.day, state, "end" if state != "running" else None),
        )

    def receipt(self, key, batch, *, state="success", errors=0, day=None):
        self.db.execute("INSERT INTO import_batches VALUES (?, 'completed', ?)", (batch, errors))
        self.db.execute(
            "INSERT INTO import_control_receipts VALUES (?, ?, ?, ?)",
            (key, batch, day or self.day, state),
        )

    def ready(self):
        return bool(self.db.execute(FTP_CYCLE_READY, {"sales_day": self.day}).fetchone()[0])

    def test_twelve_successful_receipts_without_completeness(self):
        self.cycle("files", "finished")
        for batch in range(12):
            self.receipt("files", batch)
        self.cycle("later-empty", "finished")
        self.assertTrue(self.ready())

    def test_empty_cycle_is_not_a_trigger(self):
        self.cycle("empty", "finished")
        self.assertFalse(self.ready())

    def test_previous_day_does_not_trigger(self):
        other = "2026-11-17"
        self.cycle("other", "finished", other)
        self.receipt("other", 1, day=other)
        self.assertFalse(self.ready())

    def test_failed_or_in_progress_cycle_is_not_a_trigger(self):
        self.cycle("failed", "failed")
        self.receipt("failed", 1)
        self.cycle("running", "running")
        self.receipt("running", 2)
        self.assertFalse(self.ready())

    def test_failed_file_is_not_a_trigger(self):
        self.cycle("files", "finished")
        self.receipt("files", 1, state="error", errors=3)
        self.assertFalse(self.ready())

    def test_running_cycle_defers_a_day_with_a_successful_prior_cycle(self):
        self.cycle("files", "finished")
        self.receipt("files", 1)
        self.cycle("running", "running")
        self.assertFalse(self.ready())
        self.db.execute("UPDATE import_control_cycles SET status='finished' WHERE id='running'")
        self.assertTrue(self.ready())


if __name__ == "__main__":
    unittest.main()
