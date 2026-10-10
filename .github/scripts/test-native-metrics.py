"""Verify rendered pod identity through the pinned Collector without a cluster."""

import json
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

def probe():
    """Run using only the standard library in an isolated Python container."""
    env_spec = json.loads(Path("/env.json").read_text())
    replicas = {}
    started = time.time_ns() - 2_000_000_000
    for suffix, uid in (("a", "11111111-1111-4111-8111-111111111111"),
                        ("b", "22222222-2222-4222-8222-222222222222")):
        fields = {
            "metadata.uid": uid, "metadata.name": f"checkout-{suffix}",
            "metadata.namespace": "dev", "spec.nodeName": f"node-{suffix}",
            "metadata.labels['app.kubernetes.io/component']": "checkout",
        }
        env = {}
        for entry in env_spec:
            if "value" in entry:
                # Kubernetes expands references to variables defined earlier.
                env[entry["name"]] = re.sub(r"\$\(([^)]+)\)",
                                            lambda match: env.get(match[1], match[0]), entry["value"])
            elif "fieldRef" in entry.get("valueFrom", {}):
                env[entry["name"]] = fields[entry["valueFrom"]["fieldRef"]["fieldPath"]]
        attributes = dict(item.split("=", 1) for item in env["OTEL_RESOURCE_ATTRIBUTES"].split(","))
        attributes["service.name"] = env["OTEL_SERVICE_NAME"]
        assert attributes["service.instance.id"] == uid, attributes
        assert not any("$(" in value for value in attributes.values()), attributes
        replicas[uid] = {"attributes": [{"key": key, "value": {"stringValue": value}}
                                        for key, value in attributes.items()]}

    def send(path, payload):
        request = Request("http://127.0.0.1:4318" + path, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            assert response.status == 200

    def emit(uid, count, buckets, duration_sum):
        point = {"startTimeUnixNano": str(started), "timeUnixNano": str(time.time_ns())}
        send("/v1/metrics", {"resourceMetrics": [{
            "resource": replicas[uid], "scopeMetrics": [{"scope": {"name": "native-identity-ci"}, "metrics": [
                {"name": "ci.replica.requests", "sum": {"isMonotonic": True, "aggregationTemporality": 2,
                 "dataPoints": [{**point, "asInt": str(count)}]}},
                {"name": "ci.replica.duration", "unit": "s", "histogram": {"aggregationTemporality": 2,
                 "dataPoints": [{**point, "count": str(count), "sum": duration_sum,
                                 "explicitBounds": [0.1, 0.5, 1.0], "bucketCounts": list(map(str, buckets))}]}},
            ]}]}]})

    def scrape():
        with urlopen("http://127.0.0.1:8889/metrics", timeout=5) as response:
            text = response.read().decode()
        records = []
        for line in text.splitlines():
            match = re.fullmatch(r'([\w:]+)\{(.*)\} ([\d.eE+-]+)', line)
            if match:
                records.append((match[1], dict(re.findall(r'(\w+)="([^"]*)"', match[2])), float(match[3])))
        return records

    def wait_for(check):
        deadline = time.monotonic() + 15
        records = []
        while time.monotonic() < deadline:
            try:
                records = scrape()
                if check(records):
                    return records
            except OSError:
                pass
            time.sleep(0.1)
        raise AssertionError(f"Expected Collector metrics missing: {records}")

    def streams(records, name):
        return {labels["instance"]: value for metric, labels, value in records if metric == name}

    # Wait for the Collector receiver, then reproduce the original 100/30 case.
    deadline = time.monotonic() + 15
    while True:
        try:
            send("/v1/metrics", {})
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    pod_a, pod_b = replicas
    emit(pod_a, 100, [20, 30, 40, 10], 60.0)
    emit(pod_b, 30, [10, 10, 10, 0], 9.0)
    expected = {pod_a: 100, pod_b: 30}
    records = wait_for(lambda data: streams(data, "ci_replica_requests_total") == expected
                       and streams(data, "ci_replica_duration_seconds_count") == expected)
    assert sum(streams(records, "ci_replica_requests_total").values()) == 130
    targets = {labels["instance"]: labels for metric, labels, _ in records
               if metric == "target_info" and labels.get("job") == "checkout"}
    for suffix, uid in (("a", pod_a), ("b", pod_b)):
        assert targets[uid]["k8s_pod_uid"] == uid
        assert targets[uid]["k8s_pod_name"] == f"checkout-{suffix}"
        assert targets[uid]["k8s_namespace_name"] == "dev"
        assert targets[uid]["k8s_node_name"] == f"node-{suffix}"
    # Ordinary resource metadata must not multiply labels on every native metric.
    assert all("k8s_pod_name" not in labels for metric, labels, _ in records
               if metric == "ci_replica_requests_total")

    emit(pod_a, 110, [20, 40, 40, 10], 63.0)
    expected = {pod_a: 110, pod_b: 30}
    records = wait_for(lambda data: streams(data, "ci_replica_requests_total") == expected
                       and streams(data, "ci_replica_duration_seconds_count") == expected)
    assert sum(streams(records, "ci_replica_duration_seconds_count").values()) == 140

    # Giving native streams a pod identity must preserve service-level RED totals.
    for index, (uid, resource) in enumerate(replicas.items(), 1):
        send("/v1/traces", {"resourceSpans": [{"resource": resource, "scopeSpans": [{
            "scope": {"name": "native-identity-ci"}, "spans": [{
                "traceId": f"{index:032x}", "spanId": f"{index:016x}", "name": "Checkout",
                "kind": 2, "startTimeUnixNano": str(started), "endTimeUnixNano": str(started + 10_000_000),
            }]}]}]})
    wait_for(lambda data: sum(value for metric, labels, value in data
                              if metric == "otel_demo_span_metrics_calls_total"
                              and labels.get("service_name") == "checkout"
                              and labels.get("span_kind") == "SPAN_KIND_SERVER") == 2)
    print("PASS: replica counters/histograms preserve 100 + 30 = 130, then 110 + 30 = 140;")
    print("pod metadata survives in target_info; RED still counts both replicas' requests.")


def run():
    import yaml

    ROOT = Path(__file__).resolve().parents[2]
    application = yaml.safe_load((ROOT / "platform/argocd/applications/otel-demo.yaml").read_text())
    chart = ROOT / application["spec"]["source"]["path"]
    command = ["helm", "template", "otel-demo", str(chart), "--namespace", "dev"]
    for filename in application["spec"]["source"]["helm"]["valueFiles"]:
        command += ["--values", str(chart / filename)]
    rendered = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    resources = [item for item in yaml.safe_load_all(rendered) if item]

    def resource(kind, name):
        return next(item for item in resources if item["kind"] == kind and item["metadata"]["name"] == name)

    collector = resource("Deployment", "otel-collector")["spec"]["template"]["spec"]["containers"][0]
    env = resource("Deployment", "checkout")["spec"]["template"]["spec"]["containers"][0]["env"]
    config = resource("ConfigMap", "otel-collector-config")["data"]["otel-collector-config.yaml"]
    python_image = yaml.safe_load((ROOT / "platform/monitoring/database/values.yaml").read_text())["images"]["python"]
    name = "native-metrics-ci-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="native-metrics-ci-") as directory:
        config_path, env_path = Path(directory) / "collector.yaml", Path(directory) / "env.json"
        config_path.write_text(config)
        env_path.write_text(json.dumps(env))
        mount = ["--mount", f"type=bind,src={config_path},dst=/config.yaml,readonly"]
        subprocess.run(["docker", "run", "--rm", "--network", "none", *mount,
                        collector["image"], "validate", "--config=/config.yaml"], check=True, timeout=30)
        config = yaml.safe_load(config)
        config["processors"]["batch"]["timeout"] = "100ms"
        config["connectors"]["span_metrics"]["metrics_flush_interval"] = "1s"
        config["service"]["pipelines"]["logs"]["exporters"] = ["debug"]
        config_path.write_text(yaml.safe_dump(config))
        try:
            subprocess.run(["docker", "run", "--detach", "--name", name, "--read-only",
                            "--network", "none", *mount, collector["image"], "--config=/config.yaml"],
                           check=True, stdout=subprocess.DEVNULL, timeout=30)
            subprocess.run(["docker", "run", "--rm", "--read-only", "--network", f"container:{name}",
                            "--mount", f"type=bind,src={env_path},dst=/env.json,readonly",
                            "--mount", f"type=bind,src={Path(__file__).resolve()},dst=/probe.py,readonly",
                            python_image, "python3", "/probe.py", "--probe"], check=True, timeout=55)
        finally:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe"]:
        probe()
    else:
        run()
