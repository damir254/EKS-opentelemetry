from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("keycloak_validator", ROOT / ".github/scripts/validate-kubernetes.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class KeycloakAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = []
        for chart, namespace in [("platform/keycloak", "identity"), ("platform/dashboard-access", "monitoring")]:
            rendered = subprocess.check_output(["helm", "template", "test", str(ROOT / chart), "--namespace", namespace], text=True)
            cls.resources.extend(item for item in yaml.safe_load_all(rendered) if item)

    def ingress(self, resources):
        return next(item for item in resources if item.get("kind") == "Ingress" and item["metadata"]["name"] == "keycloak-access")

    def test_admin_and_management_access_cannot_be_opened_publicly(self):
        validator.validate_keycloak(self.resources)
        for change in ("condition", "admin-path", "management"):
            with self.subTest(change=change):
                resources = deepcopy(self.resources)
                ingress = self.ingress(resources)
                if change == "condition":
                    ingress["metadata"]["annotations"].pop("alb.ingress.kubernetes.io/conditions.keycloak-admin")
                elif change == "admin-path":
                    ingress["spec"]["rules"][0]["http"]["paths"][2]["backend"]["service"]["name"] = "keycloak"
                else:
                    ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["port"]["number"] = 9000
                with self.assertRaises(ValueError):
                    validator.validate_keycloak(resources)

    def test_an_additional_ingress_cannot_bypass_source_restrictions(self):
        resources = deepcopy(self.resources)
        extra = deepcopy(self.ingress(resources))
        extra["metadata"]["name"] = "unprotected-keycloak"
        resources.append(extra)
        with self.assertRaises(ValueError):
            validator.validate_keycloak(resources)

    def test_plaintext_credentials_fail_the_guard(self):
        resources = deepcopy(self.resources)
        deployment = next(item for item in resources if item.get("kind") == "Deployment" and item["metadata"]["name"] == "keycloak")
        fields = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        password = next(item for item in fields if item["name"] == "KC_DB_PASSWORD")
        password.pop("valueFrom")
        password["value"] = "test-only-password"
        with self.assertRaises(ValueError):
            validator.validate_keycloak(resources)

    def test_coredns_only_policy_does_not_pass_on_auto_mode(self):
        resources = deepcopy(self.resources)
        policy = next(item for item in resources if item.get("kind") == "NetworkPolicy" and item["metadata"]["name"] == "keycloak")
        policy["spec"]["egress"][0]["to"] = [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}]
        with self.assertRaises(ValueError):
            validator.validate_keycloak(resources)
