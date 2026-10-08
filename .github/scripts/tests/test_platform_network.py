import importlib.util
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


model = load("network_model", ".github/scripts/network-policy-model.py")


class PlatformNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rendered = subprocess.check_output(["helm", "template", "network-tests", str(ROOT / "platform/network-policies")], text=True)
        cls.resources = [r for r in yaml.safe_load_all(rendered) if r]
        cls.config = yaml.safe_load((ROOT / "platform/network-policies/values.yaml").read_text())
        cls.policies = model.Policies(cls.resources)

    def pod(self, name):
        return model.endpoint(self.config["workloads"][name])

    def test_loki_accepts_only_its_declared_clients(self):
        loki = self.pod("loki")
        for name in ("grafana", "collector", "prometheus"):
            self.assertTrue(self.policies.permits(loki, self.pod(name), "ingress", 3100))
        for name in ("argocd-server", "external-secrets", "keycloak"):
            self.assertFalse(self.policies.permits(loki, self.pod(name), "ingress", 3100))
        wrong = dict(self.pod("grafana"), namespace="dev")
        self.assertFalse(self.policies.permits(loki, wrong, "ingress", 3100))
        self.assertFalse(self.policies.permits(loki, self.pod("grafana"), "ingress", 9096))

    def test_redis_and_repo_are_not_open_to_other_workloads(self):
        self.assertTrue(self.policies.connection(self.pod("argocd-haproxy"), self.pod("argocd-redis"), 6379))
        self.assertTrue(self.policies.connection(self.pod("argocd-server"), self.pod("argocd-repo"), 8081))
        self.assertFalse(self.policies.connection(self.pod("grafana"), self.pod("argocd-redis"), 6379))
        self.assertFalse(self.policies.connection(self.pod("external-secrets"), self.pod("argocd-repo"), 8081))

    def test_alb_health_checks_and_admission_have_bounded_sources(self):
        for target, port in (("grafana", 3000), ("argocd-server", 8080), ("keycloak", 8080)):
            self.assertTrue(self.policies.permits(self.pod(target), {"ip": "10.0.0.12"}, "ingress", port))
            self.assertFalse(self.policies.permits(self.pod(target), {"ip": "198.51.100.12"}, "ingress", port))
            self.assertFalse(self.policies.permits(self.pod(target), {"ip": "10.0.0.12"}, "ingress", 9999))
        for target, port in (("prometheus-operator", 10250), ("external-secrets-webhook", 10250), ("istiod", 15017)):
            self.assertTrue(self.policies.permits(self.pod(target), {"ip": "10.0.10.12"}, "ingress", port))

    def test_dns_api_identity_and_database_exceptions_remain_narrow(self):
        for name, config in self.config["workloads"].items():
            if not config.get("managed", True):
                continue
            pod = self.pod(name)
            self.assertTrue(self.policies.permits(pod, {"ip": "172.20.0.10"}, "egress", 53, "UDP"))
            self.assertEqual(self.policies.permits(pod, {"ip": "172.20.0.1"}, "egress", 443), bool(config.get("api")))
            self.assertEqual(self.policies.permits(pod, {"ip": "169.254.170.23"}, "egress", 80), bool(config.get("podIdentity")))
            self.assertFalse(self.policies.permits(pod, {"ip": "169.254.169.254"}, "egress", 80))
            self.assertFalse(self.policies.permits(pod, {"ip": "198.51.100.12"}, "egress", 53, "UDP"))
        for name in ("grafana", "grafana-db-bootstrap", "keycloak-db-bootstrap", "keycloak"):
            self.assertTrue(self.policies.permits(self.pod(name), {"ip": "10.0.11.5"}, "egress", 5432))
            self.assertFalse(self.policies.permits(self.pod(name), {"ip": "198.51.100.5"}, "egress", 5432))

    def test_external_domains_are_per_workload_not_allow_all_https(self):
        for name, host, port in (("grafana", "auth.damircloud.com", 443),
                                 ("argocd-repo", "release-assets.githubusercontent.com", 443),
                                 ("image-updater", "github.com", 22),
                                 ("external-secrets", "secretsmanager.eu-central-1.amazonaws.com", 443),
                                 ("external-dns", "route53.amazonaws.com", 443),
                                 ("image-updater", "123.dkr.ecr.eu-central-1.amazonaws.com", 443)):
            self.assertTrue(self.policies.permits(self.pod(name), {"domain": host}, "egress", port))
        for name in ("grafana", "argocd-repo", "external-secrets", "external-dns", "image-updater"):
            self.assertFalse(self.policies.permits(self.pod(name), {"domain": "example.com"}, "egress", 443))
        self.assertFalse(self.policies.permits(self.pod("external-secrets"), {"domain": "github.com"}, "egress", 443))

    def test_chart_ingress_cannot_override_platform_allowlists(self):
        values = yaml.safe_load((ROOT / "platform/argocd/values.yaml").read_text())
        self.assertFalse(values["global"]["networkPolicy"]["create"])
        app = yaml.safe_load((ROOT / "platform/argocd/applications/network-policies.yaml").read_text())
        self.assertLess(int(app["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"]), 0)
        self.assertFalse(any(r["kind"] in ("Job", "Role", "RoleBinding", "ServiceAccount", "ConfigMap") for r in self.resources))

    def test_one_policy_per_workload_and_no_api_name_collision(self):
        identifiers = [(p["metadata"]["namespace"], p["metadata"]["name"]) for p in self.policies.items]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        for name, workload in self.config["workloads"].items():
            owned = [p for p in self.policies.items if p["metadata"]["namespace"] == workload["namespace"]
                     and p["metadata"]["name"] == "platform-" + name]
            self.assertEqual(len(owned), 1, name)
            expected = {"Ingress", "Egress"} if workload.get("managed", True) else {"Egress"}
            self.assertEqual(set(owned[0]["spec"]["policyTypes"]), expected)
        self.assertFalse(any(p["spec"]["podSelector"] == {} and p["metadata"]["namespace"] in ("dev", "kube-system") for p in self.policies.items))

    def test_keycloak_has_one_owner_and_restricted_ingress(self):
        keycloak = self.pod("keycloak")
        self.assertTrue(self.policies.connection(self.pod("keycloak-realm"), keycloak, 8080))
        self.assertTrue(self.policies.connection(self.pod("prometheus"), keycloak, 9000))
        self.assertTrue(self.policies.connection(keycloak, keycloak, 7800))
        self.assertTrue(self.policies.connection(keycloak, keycloak, 57800))
        self.assertFalse(self.policies.permits(keycloak, self.pod("grafana"), "ingress", 8080))
        self.assertFalse(self.policies.permits(keycloak, self.pod("external-secrets"), "ingress", 9000))
        self.assertFalse((ROOT / "platform/keycloak/templates/networkpolicy.yaml").exists())

    def test_service_ports_match_routes_without_cross_product_allowances(self):
        grafana = self.pod("grafana")
        self.assertTrue(self.policies.permits(grafana, {"domain": "loki.monitoring.svc.cluster.local"}, "egress", 3100))
        self.assertFalse(self.policies.permits(grafana, {"domain": "loki.monitoring.svc.cluster.local"}, "egress", 9096))
        prometheus = self.pod("prometheus")
        self.assertTrue(self.policies.permits(prometheus, {"domain": "monitoring-grafana.monitoring.svc.cluster.local"}, "egress", 80))
        self.assertFalse(self.policies.permits(prometheus, {"domain": "monitoring-grafana-headless.monitoring.svc.cluster.local"}, "egress", 80))
        self.assertFalse(self.policies.permits(grafana, {"domain": "monitoring-grafana.monitoring.svc.cluster.local"}, "egress", 9094))



if __name__ == "__main__":
    unittest.main()
