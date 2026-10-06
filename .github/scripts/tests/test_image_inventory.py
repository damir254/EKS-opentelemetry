"""Ensure scans include runtime images hidden behind platform controllers."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("validation", Path(__file__).resolve().parents[1] / "validate-kubernetes.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class ImageInventoryTests(unittest.TestCase):
    def test_workloads_init_containers_and_controller_images_are_included(self):
        resources = [
            {"kind": "Deployment", "spec": {"template": {"spec": {
                "containers": [{"image": "quay.io/operator:v1", "args": [
                    "--prometheus-config-reloader=quay.io/reloader:v1"]}],
                "initContainers": [{"image": "busybox:1.37"}, {"image": "quay.io/operator:v1"}],
            }}}},
            {"kind": "Prometheus", "spec": {"image": "quay.io/prometheus:v1"}},
            {"kind": "Alertmanager", "spec": {"image": "quay.io/alertmanager:v1"}},
            {"kind": "ConfigMap", "metadata": {"name": "istio-sidecar-injector"}, "data": {
                "values": '{"global":{"hub":"docker.io/istio","tag":"1.31.0",'
                          '"proxy":{"image":"proxyv2"},"proxy_init":{"image":"proxyv2"}}}',
            }},
            {"kind": "Pod", "spec": {"containers": [
                {"image": "123456789012.dkr.ecr.eu-central-1.amazonaws.com/payment:abc"},
                {"image": "public.ecr.aws/aws-cli/aws-cli:2"},
            ]}},
        ]
        entries = validation.public_images(resources)["include"]
        self.assertEqual({entry["image"] for entry in entries}, {
            "quay.io/operator:v1", "quay.io/reloader:v1", "busybox:1.37",
            "quay.io/prometheus:v1", "quay.io/alertmanager:v1",
            "docker.io/istio/proxyv2:1.31.0", "public.ecr.aws/aws-cli/aws-cli:2",
        })
        self.assertEqual(len(entries), len({entry["id"] for entry in entries}))

    def test_unresolved_images_and_empty_inventory_fail(self):
        for image in ("auto", "example:${TAG}", "example:latest\nunsafe"):
            with self.subTest(image=image), self.assertRaisesRegex(ValueError, "Unresolved"):
                validation.public_images([{"kind": "Pod", "spec": {"containers": [{"image": image}]}}])
        with self.assertRaisesRegex(ValueError, "inventory size"):
            validation.public_images([])


if __name__ == "__main__":
    unittest.main()
