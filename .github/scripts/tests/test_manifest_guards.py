import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("validation", Path(__file__).resolve().parents[1] / "validate-kubernetes.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class ManifestGuardTests(unittest.TestCase):
    def test_auto_mode_only_allows_verified_eligible_instances(self):
        pool = validation.documents(validation.ROOT / "platform/auto-mode/nodepool.yaml")[0]
        requirements = pool["spec"]["template"]["spec"]["requirements"]
        instance_types = next(r for r in requirements if r["key"] == "node.kubernetes.io/instance-type")
        self.assertEqual(instance_types["operator"], "In")
        self.assertTrue(instance_types["values"])
        self.assertLessEqual(set(instance_types["values"]), {"m7i-flex.large"})
        self.assertEqual(pool["spec"]["disruption"]["consolidationPolicy"], "WhenEmptyOrUnderutilized")

    def test_custom_capacity_does_not_depend_on_builtin_nodeclass(self):
        directory = validation.ROOT / "platform/auto-mode"
        pool = validation.documents(directory / "nodepool.yaml")[0]
        nodeclass = validation.documents(directory / "nodeclass.yaml")[0]
        reference = pool["spec"]["template"]["spec"]["nodeClassRef"]
        self.assertEqual(reference["name"], nodeclass["metadata"]["name"])
        self.assertNotEqual(reference["name"], "default")
        self.assertEqual(nodeclass["spec"]["subnetSelectorTerms"][0]["tags"]["kubernetes.io/role/internal-elb"], "1")
        self.assertNotIn("id", nodeclass["spec"]["securityGroupSelectorTerms"][0])

    def test_duplicate_yaml_does_not_silently_override_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.yaml"
            path.write_text("spec:\n  replicas: 2\n  replicas: 0\n")
            with self.assertRaisesRegex(ValueError, "Duplicate YAML key"):
                validation.documents(path)

    def test_floating_workload_image_is_rejected(self):
        resource = {"kind": "Deployment", "metadata": {"name": "checkout"},
                    "spec": {"template": {"spec": {"containers": [{"name": "checkout", "image": "example/checkout:latest"}]}}}}
        with self.assertRaisesRegex(ValueError, "floating image"):
            validation.validate_images([resource])
        resource["spec"]["template"]["spec"]["containers"][0]["image"] += "@sha256:" + "a" * 64
        validation.validate_images([resource])


if __name__ == "__main__":
    unittest.main()
