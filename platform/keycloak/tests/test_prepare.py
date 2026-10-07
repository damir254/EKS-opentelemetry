import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare.py"
spec = importlib.util.spec_from_file_location("keycloak_prepare", SCRIPT)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class KeycloakPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.database = {"host": "example.rds.amazonaws.com", "port": 5432,
                         "database": "grafana", "adminUsername": "grafana_admin", "status": "available"}
        self.write({})

    def write(self, existing):
        for name, value in [("database.json", self.database), ("existing.json", existing),
                            ("admin.json", {"username": "grafana_admin", "password": "test-only:quote'\\password"})]:
            (self.work / name).write_text(json.dumps(value))

    def read_credentials(self):
        return json.loads((self.work / "application.json").read_text())

    def test_recreation_preserves_all_secrets_and_verifies_database_tls(self):
        bootstrap.prepare(self.work)
        previous = self.read_credentials()
        self.database["host"] = "replacement.rds.amazonaws.com"
        self.write(previous)
        bootstrap.prepare(self.work)
        restored = self.read_credentials()
        for field in bootstrap.SECRET_FIELDS:
            self.assertEqual(restored[field], previous[field])
            self.assertGreaterEqual(len(restored[field]), 48)
        self.assertIn("replacement.rds.amazonaws.com", restored["db_url"])
        self.assertIn("sslmode=verify-full", restored["db_url"])
        self.assertEqual((self.work / "publish-needed").read_text(), "yes")
        self.write(restored)
        bootstrap.prepare(self.work)
        self.assertEqual((self.work / "publish-needed").read_text(), "no")

    def test_bad_existing_credentials_are_never_rotated_or_logged(self):
        bootstrap.prepare(self.work)
        previous = self.read_credentials()
        for invalid in [None, [], {**previous, "username": "grafana"},
                        {**previous, "password": "test-secret-must-not-appear\n"},
                        {key: value for key, value in previous.items() if key != "configuration_client_secret"}]:
            with self.subTest(case=type(invalid).__name__):
                self.write(invalid)
                (self.work / "application.json").unlink(missing_ok=True)
                result = subprocess.run([sys.executable, str(SCRIPT), str(self.work)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("test-secret-must-not-appear", result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse((self.work / "application.json").exists())

    def test_credentials_and_sql_are_private_and_password_is_not_interpolated(self):
        bootstrap.prepare(self.work)
        previous = self.read_credentials()
        previous["password"] = "test-only-'quoted';password\\value"
        self.write(previous)
        bootstrap.prepare(self.work)
        for name in ["application.json", "admin.pgpass", "initialize.sql"]:
            self.assertEqual((self.work / name).stat().st_mode & 0o777, 0o600)
        self.assertNotIn(previous["password"], (self.work / "initialize.sql").read_text())


if __name__ == "__main__":
    unittest.main()
