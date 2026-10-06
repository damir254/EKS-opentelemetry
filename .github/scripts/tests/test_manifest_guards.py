import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("validation", Path(__file__).resolve().parents[1] / "validate-kubernetes.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class ManifestGuardTests(unittest.TestCase):
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
