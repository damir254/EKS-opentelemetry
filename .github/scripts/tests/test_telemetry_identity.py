"""Protect application instance identity through actual Argo CD values merging."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "validate-kubernetes.py"
SPEC = importlib.util.spec_from_file_location("validation", SCRIPT)
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


class TelemetryIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        application = validation.documents(validation.ROOT / "platform/argocd/applications/otel-demo.yaml")[0]
        chart = validation.ROOT / application["spec"]["source"]["path"]
        cls.command = ["helm", "template", "otel-demo", str(chart), "--namespace", "dev"]
        for filename in application["spec"]["source"]["helm"]["valueFiles"]:
            cls.command += ["--values", str(chart / filename)]
        cls.resources = cls.render()

    @classmethod
    def render(cls, extra=()):
        rendered = subprocess.run(cls.command + list(extra), capture_output=True, text=True, check=True).stdout
        return [item for item in yaml.safe_load_all(rendered) if item]

    def environment(self, resources, name):
        workload = next(item for item in resources if item["kind"] in ("Deployment", "Rollout")
                        and item["metadata"]["name"] == name)
        return workload["spec"]["template"]["spec"]["containers"][0]["env"]

    def attributes(self, resources, name):
        value = next(item["value"] for item in self.environment(resources, name)
                     if item["name"] == "OTEL_RESOURCE_ATTRIBUTES")
        return dict(item.split("=", 1) for item in value.split(","))

    def test_all_application_workloads_and_flagd_have_pod_identity(self):
        validation.validate_demo_telemetry_identity(self.resources)
        services = validation.documents(validation.ROOT / "helm/otel-demo/values.yaml")[0]["services"]
        for name in [*services, "flagd"]:
            with self.subTest(service=name):
                self.assertEqual(self.attributes(self.resources, name)["service.instance.id"], "$(K8S_POD_UID)")

    def test_namespace_criticality_and_rollout_attributes_survive(self):
        self.assertEqual(self.attributes(self.resources, "recommendation")["service.namespace"], "otel-demo")
        self.assertEqual(self.attributes(self.resources, "product-catalog")["service.criticality"], "high")
        payment = self.attributes(self.resources, "payment")
        self.assertEqual(payment["service.criticality"], "critical")
        self.assertEqual(payment["rollout.pod_template_hash"], "$(ROLLOUT_POD_TEMPLATE_HASH)")
        image = validation.documents(validation.ROOT / "helm/otel-demo/environments/dev-images.yaml")[0]
        self.assertEqual(payment["service.version"], image["services"]["payment"]["image"]["tag"])

    def test_service_overrides_cannot_replace_pod_identity(self):
        overrides = {"services": {"recommendation": {"env": {
            "OTEL_RESOURCE_ATTRIBUTES": {"value": "service.instance.id=shared,k8s.pod.name=shared,service.namespace=custom,example.attribute=a=b"},
            "OTEL_RESOURCE_ATTRIBUTES_EXTRA": {"value": "service.instance.id=also-shared,service.criticality=medium"},
        }}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.yaml"
            path.write_text(yaml.safe_dump(overrides))
            resources = self.render(["--values", str(path)])
        validation.validate_demo_telemetry_identity(resources)
        attributes = self.attributes(resources, "recommendation")
        self.assertEqual(attributes["service.namespace"], "custom")
        self.assertEqual(attributes["service.criticality"], "medium")
        self.assertEqual(attributes["example.attribute"], "a=b")
        self.assertNotIn("OTEL_RESOURCE_ATTRIBUTES_EXTRA", {item["name"] for item in self.environment(resources, "recommendation")})

    def test_static_uid_and_unexpanded_attribute_order_fail_validation(self):
        for failure in ("static-uid", "wrong-field", "wrong-order", "missing-id"):
            with self.subTest(failure=failure):
                resources = deepcopy(self.resources)
                env = self.environment(resources, "checkout")
                uid = next(item for item in env if item["name"] == "K8S_POD_UID")
                if failure == "static-uid":
                    uid.pop("valueFrom")
                    uid["value"] = "shared"
                elif failure == "wrong-field":
                    uid["valueFrom"]["fieldRef"]["fieldPath"] = "metadata.name"
                elif failure == "wrong-order":
                    env.remove(uid)
                    env.append(uid)
                else:
                    item = next(item for item in env if item["name"] == "OTEL_RESOURCE_ATTRIBUTES")
                    item["value"] = item["value"].replace("service.instance.id=$(K8S_POD_UID)", "service.instance.id=shared")
                with self.assertRaises(ValueError):
                    validation.validate_demo_telemetry_identity(resources)


if __name__ == "__main__":
    unittest.main()
