"""Reject security regressions in the actual demo Helm manifests."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

spec = importlib.util.spec_from_file_location("validation", Path(__file__).resolve().parents[1] / "validate-kubernetes.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class DemoAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = validation.ROOT
        chart = root / "helm/otel-demo"
        application = validation.documents(root / "platform/argocd/applications/otel-demo.yaml")[0]
        cls.command = ["helm", "template", "otel-demo", str(chart), "--namespace", "dev"]
        for path in application["spec"]["source"]["helm"]["valueFiles"]:
            cls.command += ["--values", str(chart / path)]
        rendered = subprocess.run(cls.command, check=True, text=True, capture_output=True).stdout
        cls.resources = [item for item in validation.yaml.load_all(rendered, Loader=validation.UniqueLoader)
                         if item is not None]

    def ingress(self, resources):
        return next(item for item in resources if item["kind"] == "Ingress")

    def test_rendered_https_hosts_and_alb_network_policy_are_valid(self):
        validation.validate_demo_access(self.resources)

    def test_http_or_missing_source_restriction_cannot_pass_ci(self):
        for key, value in [("alb.ingress.kubernetes.io/listen-ports", '[{"HTTP":80}]'),
                           ("alb.ingress.kubernetes.io/conditions.load-generator", "[]"),
                           ("alb.ingress.kubernetes.io/conditions.load-generator",
                            json.dumps([{"field": "source-ip", "sourceIpConfig": {"values": ["0.0.0.0/0"]}}]))]:
            with self.subTest(annotation=key, value=value):
                resources = deepcopy(self.resources)
                self.ingress(resources)["metadata"]["annotations"][key] = value
                with self.assertRaises(ValueError):
                    validation.validate_demo_access(resources)

    def test_additional_ingress_cannot_bypass_locust_restrictions(self):
        resources = deepcopy(self.resources)
        unprotected = deepcopy(self.ingress(resources))
        unprotected["metadata"]["name"] = "unprotected-locust"
        unprotected["spec"]["rules"] = [unprotected["spec"]["rules"][1]]
        resources.append(unprotected)
        with self.assertRaisesRegex(ValueError, "additional Ingress"):
            validation.validate_demo_access(resources)

    def test_chart_rejects_unrestricted_locust_or_same_host(self):
        for override in ["ingress.loadGenerator.allowedCIDRs[0]=0.0.0.0/0",
                         "ingress.loadGenerator.allowedCIDRs=null",
                         "ingress.loadGenerator.host=demo.damircloud.com",
                         "services.load-generator.enabled=false"]:
            with self.subTest(override=override):
                result = subprocess.run(self.command + ["--set", override], capture_output=True)
                self.assertNotEqual(result.returncode, 0, result.stdout.decode())


if __name__ == "__main__":
    unittest.main()
