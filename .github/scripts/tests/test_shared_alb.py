"""Check the combined Helm configuration and the live migration safety gate."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import unittest

import test_demo_access as demo_tests

validation = demo_tests.validation

spec = importlib.util.spec_from_file_location("migration", Path(__file__).resolve().parents[1] / "prepare-shared-alb.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class SharedALBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        demo_tests.DemoAccessTests.setUpClass()
        chart = validation.ROOT / "platform/dashboard-access"
        cls.command = ["helm", "template", "dashboard-access", str(chart), "--namespace", "monitoring",
                       "--values", str(chart / "values.yaml")]
        rendered = subprocess.run(cls.command, check=True, text=True, capture_output=True).stdout
        cls.resources = deepcopy(demo_tests.DemoAccessTests.resources) + [
            item for item in validation.yaml.load_all(rendered, Loader=validation.UniqueLoader) if item is not None]

    def test_all_ingresses_share_one_group_with_certificates_and_per_service_restrictions(self):
        validation.validate_demo_access(self.resources)
        validation.validate_shared_alb(self.resources)
        argocd = next(item for item in self.resources if item["kind"] == "Ingress"
                      and item["metadata"]["name"] == "argocd-dashboard")
        self.assertEqual(argocd["metadata"]["annotations"]["alb.ingress.kubernetes.io/backend-protocol"], "HTTPS")

    def test_dashboard_condition_cannot_be_removed(self):
        for name, service in [("argocd-dashboard", "argocd-server"), ("grafana-dashboard", "monitoring-grafana")]:
            with self.subTest(ingress=name):
                resources = deepcopy(self.resources)
                ingress = next(item for item in resources if item["kind"] == "Ingress" and item["metadata"]["name"] == name)
                del ingress["metadata"]["annotations"][f"alb.ingress.kubernetes.io/conditions.{service}"]
                with self.assertRaisesRegex(ValueError, "source-IP"):
                    validation.validate_shared_alb(resources)

    def test_an_additional_alb_group_or_separate_demo_class_is_rejected(self):
        resources = deepcopy(self.resources)
        duplicate = deepcopy(next(item for item in resources if item["kind"] == "IngressClassParams"))
        duplicate["metadata"]["name"] = "second-alb"
        resources.append(duplicate)
        with self.assertRaisesRegex(ValueError, "Exactly one"):
            validation.validate_shared_alb(resources)
        resources = deepcopy(self.resources)
        demo = next(item for item in resources if item["kind"] == "Ingress" and item["metadata"]["name"] == "otel-demo")
        demo["spec"]["ingressClassName"] = "alb"
        with self.assertRaisesRegex(ValueError, "shared ALB"):
            validation.validate_shared_alb(resources)

    def test_empty_certificates_or_world_dashboard_access_cannot_render(self):
        for override in ["certificateARNs=null", "allowedCIDRs[0]=0.0.0.0/0"]:
            with self.subTest(override=override):
                result = subprocess.run(self.command + ["--set", override], capture_output=True)
                self.assertNotEqual(result.returncode, 0)


class LiveMigrationGateTests(unittest.TestCase):
    def rules(self):
        return [
            {"IsDefault": True, "Actions": [{"Type": "fixed-response", "FixedResponseConfig": {"StatusCode": "404"}}]},
            {"IsDefault": False, "Actions": [{"Type": "forward"}], "Conditions": [
                {"Field": "host-header", "HostHeaderConfig": {"Values": ["argocd.damircloud.com"]}},
                {"Field": "source-ip", "SourceIpConfig": {"Values": ["109.175.0.0/16"]}},
            ]},
        ]

    def test_protected_listener_rule_passes(self):
        migration.verify_rules(self.rules(), {"argocd.damircloud.com": ["109.175.0.0/16"]})

    def test_unrestricted_rule_default_forward_or_wildcard_bypass_blocks_migration(self):
        cases = []
        rules = self.rules()
        rules[1]["Conditions"].pop()
        cases.append(rules)
        rules = self.rules()
        rules[0]["Actions"] = [{"Type": "forward"}]
        cases.append(rules)
        rules = self.rules()
        rules.append({"IsDefault": False, "Actions": [{"Type": "forward"}], "Conditions": [
            {"Field": "host-header", "HostHeaderConfig": {"Values": ["*.damircloud.com"]}},
        ]})
        cases.append(rules)
        for rules in cases:
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                migration.verify_rules(rules, {"argocd.damircloud.com": ["109.175.0.0/16"]})


if __name__ == "__main__":
    unittest.main()
