"""Run opt-in network checks in disposable namespaces; never edit platform pods."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import time
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVER = '''import http.server, threading, time
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"network-test")
    def log_message(self, *args): pass
for port in PORTS:
    threading.Thread(target=http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever, daemon=True).start()
while True: time.sleep(3600)
'''
PROBE = '''import json, ssl, sys, urllib.request, urllib.error
url, expected = sys.argv[1:]
try:
    context = ssl.create_default_context(cafile="/var/run/network-test-ca/ca.crt") if url.startswith("https://kubernetes.default.svc/") else None
    with urllib.request.urlopen(url, timeout=8, context=context) as r: r.read(1)
    allowed = True
except urllib.error.HTTPError:
    allowed = True  # A response proves connectivity; no AWS credentials are used.
except (urllib.error.URLError, TimeoutError, OSError):
    allowed = False
print(json.dumps({"url": url, "reachable": allowed, "expected": expected == "allow"}))
raise SystemExit(0 if allowed == (expected == "allow") else 1)
'''


def kubectl(*args, data=None):
    result = subprocess.run(["kubectl", *args], input=data, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def rewrite(value, namespaces):
    if isinstance(value, dict):
        return {key: namespaces.get(item, item) if key == "kubernetes.io/metadata.name" else rewrite(item, namespaces)
                for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite(item, namespaces) for item in value]
    if isinstance(value, str):
        for original, replacement in namespaces.items():
            value = value.replace(f".{original}.svc.cluster.local", f".{replacement}.svc.cluster.local")
    return value


def workload(name, config, namespaces, ports, image):
    labels = dict(config["selector"])
    labels["network-smoke-test"] = name
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {
        "name": name, "namespace": namespaces[config["namespace"]]}, "spec": {
        "replicas": 1, "selector": {"matchLabels": {"network-smoke-test": name}}, "template": {
            "metadata": {"labels": labels, "annotations": {"sidecar.istio.io/inject": "false"}},
            "spec": {"automountServiceAccountToken": False, "terminationGracePeriodSeconds": 1,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{"name": "test", "image": image,
                    "command": ["python3", "-B", "-c", SERVER.replace("PORTS", repr(ports))],
                    "volumeMounts": [{"name": "api-ca", "mountPath": "/var/run/network-test-ca", "readOnly": True}],
                    "securityContext": {"allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                    "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                                  "limits": {"cpu": "100m", "memory": "96Mi"}}}],
                "volumes": [{"name": "api-ca", "configMap": {"name": "kube-root-ca.crt"}}]}}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Create, test and delete isolated workloads on the current kube context")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly run disposable checks on the current cluster")
    config = yaml.safe_load((ROOT / "platform/network-policies/values.yaml").read_text())
    # Reuse the existing pinned bootstrap image; the policy chart has no workloads.
    test_image = yaml.safe_load((ROOT / "platform/monitoring/database/values.yaml").read_text())["images"]["python"]
    rendered = subprocess.check_output(["helm", "template", "network-test", str(ROOT / "platform/network-policies")], text=True)
    prefix = "network-test-" + uuid.uuid4().hex[:8]
    namespaces = {name: f"{prefix}-{suffix}" for name, suffix in (
        ("monitoring", "mon"), ("argocd", "argo"), ("external-secrets", "eso"), ("dev", "demo"), ("outsider", "other"))}
    roles = {"grafana": [3000, 9094], "loki": [3100], "prometheus": [9090], "collector": [8888, 8889],
             "argocd-repo": [8081], "argocd-server": [8080], "argocd-redis": [6379, 26379],
             "argocd-haproxy": [6379], "external-secrets": [9999]}
    selected = {"platform-" + name for name in roles} | {"platform-default-deny"}
    objects = []
    for namespace in namespaces.values():
        objects.append({"apiVersion": "v1", "kind": "Namespace", "metadata": {
            "name": namespace, "labels": {"network-smoke-test": prefix, "pod-security.kubernetes.io/enforce": "restricted"}}})
    for item in yaml.safe_load_all(rendered):
        if item and item["kind"] in ("NetworkPolicy", "ApplicationNetworkPolicy") and item["metadata"]["name"] in selected:
            original = item["metadata"]["namespace"]
            if original not in namespaces:
                continue
            item["metadata"]["namespace"] = namespaces[original]
            item["metadata"].pop("annotations", None)
            item = rewrite(item, namespaces)
            objects.append(item)
    # Exercise the Collector's real existing demo policies, plus its new Service
    # DNS allowance, instead of assuming an unrestricted ingestion client.
    demo = ROOT / "helm/otel-demo"
    app = yaml.safe_load((ROOT / "platform/argocd/applications/otel-demo.yaml").read_text())
    flags = [argument for file in app["spec"]["source"]["helm"]["valueFiles"] for argument in ("-f", str(demo / file))]
    output = subprocess.check_output(["helm", "template", "otel-demo", str(demo), "-n", "dev", *flags], text=True)
    for item in yaml.safe_load_all(output):
        if not item or item["kind"] != "NetworkPolicy":
            continue
        selector = item["spec"]["podSelector"].get("matchLabels", {})
        if not all(config["workloads"]["collector"]["selector"].get(k) == v for k, v in selector.items()):
            continue
        if item["spec"]["podSelector"].get("matchExpressions"):
            continue  # Istio-specific rules select different demo workloads.
        item["metadata"]["namespace"] = namespaces["dev"]
        objects.append(rewrite(item, namespaces))
    # Both outsiders have no allowlists: one inside monitoring, one outside all managed namespaces.
    role_config = deepcopy(config["workloads"])
    role_config["inside-rogue"] = {"namespace": "monitoring", "selector": {"role": "rogue"}}
    role_config["outside-rogue"] = {"namespace": "outsider", "selector": {"role": "rogue"}}
    roles.update({"inside-rogue": [3100], "outside-rogue": [3100]})
    for name, ports in roles.items():
        objects.append(workload(name, role_config[name], namespaces, ports, test_image))
        services = role_config[name].get("services", {name: {str(port): [port] for port in ports}})
        for service, mapping in services.items():
            service_ports = [{"name": f"p{exposed}", "port": exposed, "targetPort": int(port)}
                             for port, exposed_ports in mapping.items() if int(port) in ports for exposed in exposed_ports]
            if service_ports:
                objects.append({"apiVersion": "v1", "kind": "Service", "metadata": {
                    "name": service, "namespace": namespaces[role_config[name]["namespace"]]}, "spec": {
                    "selector": {"network-smoke-test": name}, "ports": service_ports}})
    try:
        print("Disposable network checks:", prefix, flush=True)
        kubectl("apply", "-f", "-", data=yaml.safe_dump_all(objects))
        for namespace in namespaces.values():
            kubectl("wait", "--for=condition=Available", "deployment", "--all", "-n", namespace, "--timeout=180s")
        deadline = time.monotonic() + 120
        expected = {p["metadata"]["name"] for p in objects if p["kind"] in ("NetworkPolicy", "ApplicationNetworkPolicy") and p["spec"]["podSelector"]}
        while time.monotonic() < deadline:
            compiled = set()
            for namespace in namespaces.values():
                records = json.loads(kubectl("get", "policyendpoints", "-n", namespace, "-o", "json"))["items"]
                compiled.update(p["spec"]["policyRef"]["name"] for p in records if p["spec"].get("podSelectorEndpoints"))
            if expected <= compiled:
                break
            time.sleep(2)
        else:
            raise RuntimeError("Test policies did not compile")
        def internal(name, port):
            namespace = namespaces[role_config[name]["namespace"]]
            services = role_config[name].get("services", {name: {str(port): [port]}})
            service, mapping = next((service, mapping) for service, mapping in services.items() if str(port) in mapping)
            return f"http://{service}.{namespace}.svc.cluster.local:{mapping[str(port)][0]}/"
        cases = [
            ("grafana", internal("loki", 3100), "allow"),
            ("grafana", internal("prometheus", 9090), "allow"),
            ("collector", internal("loki", 3100), "allow"),
            ("prometheus", internal("collector", 8888), "allow"),
            ("prometheus", internal("collector", 8889), "allow"),
            ("grafana", internal("collector", 8888), "deny"),
            ("outside-rogue", internal("collector", 8888), "deny"),
            ("grafana", "https://auth.damircloud.com/realms/platform/.well-known/openid-configuration", "allow"),
            ("outside-rogue", internal("loki", 3100), "deny"),
            ("inside-rogue", internal("loki", 3100), "deny"),
            ("grafana", internal("outside-rogue", 3100), "deny"),
            ("grafana", "https://example.com/", "deny"),
            ("argocd-server", internal("argocd-repo", 8081), "allow"),
            ("argocd-server", internal("argocd-haproxy", 6379), "allow"),
            ("argocd-haproxy", internal("argocd-redis", 6379), "allow"),
            ("argocd-repo", internal("argocd-haproxy", 6379), "allow"),
            ("outside-rogue", internal("argocd-redis", 6379), "deny"),
            ("argocd-repo", "https://argoproj.github.io/argo-helm/index.yaml", "allow"),
            ("argocd-repo", "https://github.com/damir254/EKS-opentelemetry", "allow"),
            ("argocd-repo", "https://example.com/", "deny"),
            ("external-secrets", f"https://secretsmanager.{config['region']}.amazonaws.com/", "allow"),
            ("external-secrets", "http://169.254.170.23/", "allow"),
            ("grafana", "https://kubernetes.default.svc/", "allow"),
            ("external-secrets", "https://kubernetes.default.svc/", "allow"),
            ("external-secrets", "https://example.com/", "deny"),
        ]
        failures = []
        for role, url, expected_result in cases:
            namespace = namespaces[role_config[role]["namespace"]]
            result = subprocess.run(["kubectl", "exec", "-n", namespace, f"deployment/{role}", "--",
                                     "python3", "-B", "-c", PROBE, url, expected_result], text=True, capture_output=True)
            print(role + ": " + result.stdout.strip(), flush=True)
            if result.returncode:
                failures.append(f"{role}: {url} should {expected_result}")
        if failures:
            raise RuntimeError("Network checks failed:\n" + "\n".join(failures))
        print(f"All {len(cases)} live connectivity checks passed", flush=True)
    finally:
        for namespace in namespaces.values():
            # Names are generated for this run; never delete preexisting platform namespaces.
            kubectl("delete", "namespace", namespace, "--ignore-not-found", "--wait=false")
        print("Disposable test namespace cleanup requested", flush=True)


if __name__ == "__main__":
    main()
