"""Recovery must target the synced Secret and never put passwords in arguments."""

import base64
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("recovery", Path(__file__).resolve().parents[1] / "reset-grafana-admin.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class GrafanaAdminTests(unittest.TestCase):
    def setUp(self):
        self.password = "test-only-private-dashboard-password"
        self.external = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        self.deployment = {
            "metadata": {"generation": 4},
            "spec": {"replicas": 2, "template": {"spec": {"containers": [{"name": "grafana", "env": [{
                "name": "GF_SECURITY_ADMIN_PASSWORD", "valueFrom": {"secretKeyRef": {
                    "name": recovery.SECRET, "key": "admin-password"}}}]}]}}},
            "status": {"observedGeneration": 4, "updatedReplicas": 2, "readyReplicas": 2, "replicas": 2},
        }
        self.secret = {"data": {key: base64.b64encode(value.encode()).decode() for key, value in
                                [("admin-user", "admin"), ("admin-password", self.password)]}}

    def replies(self, verification="200"):
        return [subprocess.CompletedProcess([], 0, json.dumps(value), "") for value in
                [self.external, self.deployment, self.secret]] + [
            subprocess.CompletedProcess([], 0, "Admin password changed successfully", ""),
            subprocess.CompletedProcess([], 0, verification, "")]

    def test_reset_uses_configured_rds_and_stdin_then_verifies_login(self):
        output = io.StringIO()
        with patch.object(recovery.subprocess, "run", side_effect=self.replies()) as run, redirect_stdout(output):
            recovery.reset()
        self.assertNotIn(self.password, output.getvalue())
        for call in run.call_args_list:
            self.assertNotIn(self.password, " ".join(call.args[0]))
        command = run.call_args_list[3]
        self.assertIn("/etc/grafana/grafana.ini", command.args[0])
        self.assertIn("--password-from-stdin", command.args[0])
        self.assertEqual(command.kwargs["input"], self.password + "\n")
        self.assertEqual(json.loads(run.call_args_list[4].kwargs["input"])["password"], self.password)

    def test_unsynced_secret_or_old_deployment_never_resets(self):
        cases = ["secret", "deployment", "rollout"]
        for case in cases:
            with self.subTest(case=case):
                self.setUp()
                if case == "secret":
                    self.external["status"]["conditions"][0]["status"] = "False"
                elif case == "deployment":
                    self.deployment["spec"]["template"]["spec"]["containers"][0]["env"][0]["valueFrom"]["secretKeyRef"]["name"] = "monitoring-grafana"
                else:
                    self.deployment["status"]["updatedReplicas"] = 1
                with patch.object(recovery.subprocess, "run", side_effect=self.replies()) as run:
                    with self.assertRaises(RuntimeError):
                        recovery.reset()
                self.assertFalse(any("exec" in call.args[0] for call in run.call_args_list))

    def test_login_failure_is_reported_without_password(self):
        with patch.object(recovery.subprocess, "run", side_effect=self.replies("401")):
            with self.assertRaises(RuntimeError) as error:
                recovery.reset()
        self.assertNotIn(self.password, str(error.exception))


if __name__ == "__main__":
    unittest.main()
