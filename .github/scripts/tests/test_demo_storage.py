"""Protect consolidation, cleanup and the first Deployment-to-StatefulSet cutover."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import unittest

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "validate-kubernetes.py"
SPEC = importlib.util.spec_from_file_location("validation", SCRIPT)
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


class DemoStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        application = validation.documents(validation.ROOT / "platform/argocd/applications/otel-demo.yaml")[0]
        chart = validation.ROOT / application["spec"]["source"]["path"]
        cls.command = ["helm", "template", "otel-demo", str(chart), "--namespace", "dev"]
        for filename in application["spec"]["source"]["helm"]["valueFiles"]:
            cls.command += ["--values", str(chart / filename)]
        rendered = subprocess.run(cls.command, capture_output=True, text=True, check=True).stdout
        cls.resources = [item for item in yaml.safe_load_all(rendered) if item]
        cls.resources += validation.documents(validation.ROOT / "platform/storage/gp3-storageclass.yaml")

    def resource(self, resources, kind, name):
        return next(item for item in resources if item["kind"] == kind and item["metadata"]["name"] == name)

    def test_rendered_dependencies_allow_consolidation_and_persist_data(self):
        validation.validate_demo_storage(self.resources)

    def test_services_exclude_old_deployment_pods(self):
        for name in ["astronomy-db", "kafka", "valkey-cart"]:
            with self.subTest(dependency=name):
                labels = self.resource(self.resources, "StatefulSet", name)["spec"]["template"]["metadata"]["labels"]
                old_labels = {key: value for key, value in labels.items() if key != "app.kubernetes.io/workload-kind"}
                selector = self.resource(self.resources, "Service", name)["spec"]["selector"]
                self.assertFalse(all(old_labels.get(key) == value for key, value in selector.items()))
                changed = deepcopy(self.resources)
                self.resource(changed, "Service", name)["spec"]["selector"] = old_labels
                with self.assertRaisesRegex(ValueError, "Service must select only"):
                    validation.validate_demo_storage(changed)

    def test_blocked_disruption_or_wrong_cleanup_fails_validation(self):
        for name in ["astronomy-db", "kafka", "valkey-cart"]:
            with self.subTest(dependency=name):
                changed = deepcopy(self.resources)
                spec = self.resource(changed, "StatefulSet", name)["spec"]
                spec["template"]["metadata"]["annotations"] = {"karpenter.sh/do-not-disrupt": "true"}
                with self.assertRaisesRegex(ValueError, "blocks demo consolidation"):
                    validation.validate_demo_storage(changed)
                spec["template"]["metadata"].pop("annotations")
                spec["persistentVolumeClaimRetentionPolicy"]["whenScaled"] = "Delete"
                with self.assertRaisesRegex(ValueError, "retain them during scale-down"):
                    validation.validate_demo_storage(changed)
        changed = deepcopy(self.resources)
        self.resource(changed, "StorageClass", "gp3")["reclaimPolicy"] = "Retain"
        with self.assertRaisesRegex(ValueError, "delete the EBS volume"):
            validation.validate_demo_storage(changed)

    def test_kafka_metadata_cannot_be_left_on_ephemeral_storage(self):
        changed = deepcopy(self.resources)
        env = self.resource(changed, "StatefulSet", "kafka")["spec"]["template"]["spec"]["containers"][0]["env"]
        next(item for item in env if item["name"] == "KAFKA_METADATA_LOG_DIR")["value"] = "/tmp/kraft"
        with self.assertRaisesRegex(ValueError, "KRaft metadata must both use the PVC"):
            validation.validate_demo_storage(changed)

    def test_missing_persistence_configuration_fails_helm_schema(self):
        for name in ["postgres", "kafka", "valkey"]:
            with self.subTest(dependency=name):
                result = subprocess.run(self.command + ["--set", name + ".persistence=null"], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("persistence", result.stderr)


if __name__ == "__main__":
    unittest.main()
