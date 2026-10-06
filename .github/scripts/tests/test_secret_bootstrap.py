"""Render the real demo and check that secret consumers cannot block creation."""

from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]


class SecretBootstrapTests(unittest.TestCase):
    def test_external_secrets_precede_their_workloads(self):
        application = yaml.safe_load(
            (ROOT / "platform/argocd/applications/otel-demo.yaml").read_text()
        )
        chart = ROOT / application["spec"]["source"]["path"]
        command = ["helm", "template", "otel-demo", str(chart), "--namespace", "dev"]
        for filename in application["spec"]["source"]["helm"]["valueFiles"]:
            command += ["--values", str(chart / filename)]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        resources = [resource for resource in yaml.safe_load_all(result.stdout) if resource]
        providers = {
            resource["spec"].get("target", {}).get("name", resource["metadata"]["name"]): resource
            for resource in resources if resource.get("kind") == "ExternalSecret"
        }
        self.assertTrue(providers, "The demo must render its database ExternalSecrets")
        checked = set()
        for resource in resources:
            pod = resource.get("spec", {}).get("template", {}).get("spec", {})
            for container in pod.get("containers", []) + pod.get("initContainers", []):
                for env in container.get("env", []):
                    secret = env.get("valueFrom", {}).get("secretKeyRef", {}).get("name")
                    if secret not in providers:
                        continue
                    provider = providers[secret]
                    secret_wave = int(provider["metadata"].get("annotations", {}).get("argocd.argoproj.io/sync-wave", "0"))
                    workload_wave = int(resource["metadata"].get("annotations", {}).get("argocd.argoproj.io/sync-wave", "0"))
                    with self.subTest(workload=resource["metadata"]["name"], secret=secret):
                        self.assertLess(secret_wave, workload_wave,
                                        "Missing secrets must be synced before waiting for workload health")
                    checked.add(secret)
        self.assertEqual(checked, set(providers), "Check every rendered database secret consumer")


if __name__ == "__main__":
    unittest.main()
