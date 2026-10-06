"""Check ruleset safety without changing repository settings."""

import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "required_checks", Path(__file__).resolve().parents[1] / "require-infrastructure-checks.py"
)
required_checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(required_checks)


class RequiredChecksTests(unittest.TestCase):
    def configure(self, keys, workflow_exists=True, rulesets=None):
        payloads = []

        def api(path):
            if "/contents/" in path:
                if not workflow_exists:
                    raise RuntimeError("Workflow has not been pushed")
                return {}
            if path.endswith("/keys"):
                return keys
            if path == "apps/github-actions":
                return {"id": 123}
            if path.endswith("/rulesets"):
                return rulesets or []
            self.fail(f"Unexpected API request: {path}")

        def write(args, **kwargs):
            payload = json.loads(Path(args[args.index("--input") + 1]).read_text())
            payloads.append((args[args.index("--method") + 1], payload))

        with patch.object(required_checks, "api", side_effect=api), \
                patch.object(required_checks.subprocess, "run", side_effect=write), \
                patch("sys.argv", ["require-infrastructure-checks.py"]), patch("builtins.print"):
            required_checks.main()
        return payloads

    def test_ruleset_requires_both_checks_and_uses_github_deploy_key_contract(self):
        keys = [{"title": "Argo CD Image Updater EKS", "read_only": False},
                {"title": "Read-only checkout", "read_only": True}]
        [(method, payload)] = self.configure(keys)
        self.assertEqual(method, "POST")
        self.assertEqual(payload["bypass_actors"], [
            {"actor_id": None, "actor_type": "DeployKey", "bypass_mode": "always"}
        ])
        checks = payload["rules"][0]["parameters"]["required_status_checks"]
        self.assertEqual(checks, [
            {"context": "Terraform validation", "integration_id": 123},
            {"context": "Kubernetes and workflow validation", "integration_id": 123},
        ])

    def test_extra_writable_key_blocks_broad_bypass(self):
        keys = [{"title": "Argo CD Image Updater EKS", "read_only": False},
                {"title": "Other automation", "read_only": False}]
        with patch.object(required_checks.subprocess, "run") as mutation:
            with self.assertRaisesRegex(SystemExit, "sole writable deploy key"):
                self.configure(keys)
            mutation.assert_not_called()

    def test_unpublished_workflow_blocks_ruleset_activation(self):
        with self.assertRaisesRegex(RuntimeError, "Workflow has not been pushed"):
            self.configure([], workflow_exists=False)

    def test_existing_owned_ruleset_is_updated(self):
        [(method, _)] = self.configure(
            [{"title": "Argo CD Image Updater EKS", "read_only": False}],
            rulesets=[{"id": 456, "name": "Required infrastructure CI"}],
        )
        self.assertEqual(method, "PUT")


if __name__ == "__main__":
    unittest.main()
