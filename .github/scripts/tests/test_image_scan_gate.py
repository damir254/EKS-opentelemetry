"""A scan must distinguish fixable blockers, unfixed findings and invalid reports."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "check-image-scan.py"


class ImageScanGateTests(unittest.TestCase):
    def invoke(self, report):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.json"
            path.write_text(json.dumps(report))
            env = dict(os.environ, GITHUB_STEP_SUMMARY=str(Path(directory) / "summary.md"))
            return subprocess.run(["python3", str(SCRIPT), "--report", str(path),
                                   "--image", "example/proxy:1"], env=env, capture_output=True, text=True)

    def finding(self, severity, fixed=""):
        return {"PkgName": "openssl", "VulnerabilityID": "CVE-2026-84782",
                "Severity": severity, "FixedVersion": fixed}

    def test_fixable_high_or_critical_fails_the_job(self):
        for severity in ("HIGH", "CRITICAL"):
            with self.subTest(severity=severity):
                result = self.invoke({"SchemaVersion": 2, "Results": [{"Vulnerabilities": [
                    self.finding(severity, "3.0.2-0ubuntu1.30")]}]})
                self.assertEqual(result.returncode, 1)
                self.assertIn("fixable: 1", result.stdout)

    def test_unfixed_entries_are_reported_without_blocking(self):
        result = self.invoke({"SchemaVersion": 2, "Results": [{"Vulnerabilities": [
            self.finding("CRITICAL"), self.finding("MEDIUM", "fixed")]}]})
        self.assertEqual(result.returncode, 0)
        self.assertIn("Critical: 1; fixable: 0; unfixed: 1", result.stdout)

    def test_clean_scan_passes_and_incomplete_report_fails(self):
        self.assertEqual(self.invoke({"SchemaVersion": 2, "Results": []}).returncode, 0)
        self.assertNotEqual(self.invoke({}).returncode, 0)


if __name__ == "__main__":
    unittest.main()
