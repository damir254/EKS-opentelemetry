import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("database_recovery", Path(__file__).parents[1] / "database-recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class DatabaseRecoveryTests(unittest.TestCase):
    def snapshot(self, stamp, **changes):
        return {"DBInstanceIdentifier": "test-instance", "Status": "available", "Engine": "postgres",
                "Encrypted": True, "SnapshotCreateTime": stamp, **changes}

    def test_latest_valid_snapshot_wins_over_newer_unsafe_snapshots(self):
        valid = self.snapshot("2026-10-01T00:00:00Z")
        snapshots = [self.snapshot("2026-09-01T00:00:00Z"), valid,
                     self.snapshot("2026-10-02T00:00:00Z", Encrypted=False),
                     self.snapshot("2026-10-03T00:00:00Z", Status="creating"),
                     self.snapshot("2026-10-04T00:00:00Z", DBInstanceIdentifier="another-instance")]
        self.assertEqual(recovery.latest_snapshot(snapshots, "test-instance"), valid)

    def test_missing_backup_does_not_silently_create_an_empty_database(self):
        with self.assertRaises(ValueError):
            recovery.latest_snapshot([], "test-instance")
