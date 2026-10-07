"""Credential continuity and failure checks for the RDS bootstrap."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare.py"
spec = importlib.util.spec_from_file_location("prepare", SCRIPT)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class CredentialPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.database = {
            "host": "replacement.example.rds.amazonaws.com", "port": 5432,
            "database": "grafana", "adminUsername": "grafana_admin", "status": "available",
        }
        self.admin = {"username": "grafana_admin", "password": "test-only:admin\\password"}
        self.existing = {
            "host": "old.example.rds.amazonaws.com:5432", "username": "grafana",
            "password": "test-only-'quoted';password\\value",
            "secret_key": "test-only-shared-key-01234567890123456789",
        }
        self.write_inputs({})

    def write_inputs(self, existing):
        for filename, value in (("database.json", self.database), ("admin.json", self.admin),
                                ("existing.json", existing)):
            (self.directory / filename).write_text(json.dumps(value))

    def credentials(self):
        return json.loads((self.directory / "application.json").read_text())

    def test_fresh_credentials_are_private_and_shared(self):
        bootstrap.prepare(self.directory)
        credentials = self.credentials()
        self.assertEqual(credentials["username"], "grafana")
        self.assertGreaterEqual(len(credentials["password"]), 48)
        self.assertEqual(len(credentials["secret_key"]), 64)
        self.assertEqual(credentials["grafana_admin_user"], "admin")
        self.assertGreaterEqual(len(credentials["grafana_admin_password"]), 48)
        self.assertNotEqual(credentials["grafana_admin_password"], credentials["password"])
        for name in ("application.json", "admin.pgpass", "initialize.sql"):
            self.assertEqual((self.directory / name).stat().st_mode & 0o777, 0o600)

    def test_recreation_updates_endpoint_without_rotating_credentials(self):
        self.write_inputs(self.existing)
        bootstrap.prepare(self.directory)
        credentials = self.credentials()
        self.assertEqual(credentials["host"], self.database["host"] + ":5432")
        self.assertEqual(credentials["password"], self.existing["password"])
        self.assertEqual(credentials["secret_key"], self.existing["secret_key"])
        self.assertEqual((self.directory / "publish-needed").read_text(), "yes")

    def test_upgrade_adds_dashboard_login_without_changing_database_credentials(self):
        self.write_inputs(self.existing)
        bootstrap.prepare(self.directory)
        credentials = self.credentials()
        self.assertEqual(credentials["password"], self.existing["password"])
        self.assertEqual(credentials["secret_key"], self.existing["secret_key"])
        self.assertEqual(credentials["grafana_admin_user"], "admin")
        self.assertNotIn(credentials["grafana_admin_password"],
                         (self.directory / "initialize.sql").read_text())
        self.write_inputs(credentials)
        bootstrap.prepare(self.directory)
        self.assertEqual(self.credentials(), credentials)
        self.assertEqual((self.directory / "publish-needed").read_text(), "no")

    def test_dashboard_credentials_survive_database_recreation(self):
        previous = {**self.existing, "grafana_admin_user": "admin",
                    "grafana_admin_password": "test-only-dashboard-password"}
        self.write_inputs(previous)
        bootstrap.prepare(self.directory)
        self.assertEqual(self.credentials()["grafana_admin_password"], previous["grafana_admin_password"])

    def test_invalid_dashboard_credentials_are_not_replaced_or_logged(self):
        cases = [{"grafana_admin_user": "admin"},
                 {"grafana_admin_user": "another_user", "grafana_admin_password": "test-private"},
                 {"grafana_admin_user": "admin", "grafana_admin_password": "test-private\n"},
                 {"grafana_admin_user": "admin", "grafana_admin_password": None}]
        for fields in cases:
            with self.subTest(fields=list(fields)):
                self.write_inputs({**self.existing, **fields})
                result = subprocess.run([sys.executable, str(SCRIPT), str(self.directory)],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("test-private", result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse((self.directory / "application.json").exists())

    def test_retry_keeps_encryption_key_and_avoids_new_secret_version(self):
        bootstrap.prepare(self.directory)
        previous = self.credentials()
        self.write_inputs(previous)
        bootstrap.prepare(self.directory)
        self.assertEqual(self.credentials(), previous)
        self.assertEqual((self.directory / "publish-needed").read_text(), "no")

    def test_unrelated_secret_is_not_overwritten(self):
        self.write_inputs({**self.existing, "username": "another_application"})
        with self.assertRaises(ValueError):
            bootstrap.prepare(self.directory)
        self.assertFalse((self.directory / "application.json").exists())

    def test_non_object_secret_is_not_treated_as_new(self):
        for value in (None, [], ""):
            with self.subTest(value=value):
                self.write_inputs(value)
                with self.assertRaises(ValueError):
                    bootstrap.prepare(self.directory)
                self.assertFalse((self.directory / "application.json").exists())

    def test_invalid_secret_is_not_logged(self):
        invalid = {**self.existing, "password": "test-secret-that-must-not-appear\n"}
        self.write_inputs(invalid)
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.directory)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("test-secret-that-must-not-appear", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_wrong_database_is_not_initialized(self):
        self.database["database"] = "another_application"
        self.write_inputs({})
        with self.assertRaises(ValueError):
            bootstrap.prepare(self.directory)

    def test_restore_can_omit_database_name_metadata(self):
        self.database["database"] = None
        self.write_inputs(self.existing)
        bootstrap.prepare(self.directory)
        self.assertEqual(self.credentials()["password"], self.existing["password"])


if __name__ == "__main__":
    unittest.main()
