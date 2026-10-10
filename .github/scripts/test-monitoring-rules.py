"""Validate workload ownership, dependency alerts and their Grafana queries."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pipeline", ROOT / ".github/scripts/test-telemetry-pipeline.py")
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)

# Same kube-state-metrics version as kube-prometheus-stack 88.3.0.
KSM_IMAGE = "registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.19.1@sha256:85108987d044b18a098126732f98602df408888c0f7d456241f5abefb9744bc1"


def check_rollout_metrics(directory):
    values = yaml.safe_load((ROOT / "platform/monitoring/monitoring-values.yaml").read_text())
    config = directory / "rollout-config.yaml"
    config.write_text(yaml.safe_dump(values["kube-state-metrics"]["customResourceState"]["config"]))
    kubeconfig = directory / "kubeconfig"
    kubeconfig.write_text(yaml.safe_dump({"apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": "mock", "cluster": {"server": "http://127.0.0.1:40443"}}],
        "contexts": [{"name": "mock", "context": {"cluster": "mock", "user": "mock"}}],
        "current-context": "mock", "users": [{"name": "mock", "user": {}}]}))
    python_image = yaml.safe_load((ROOT / "platform/monitoring/database/values.yaml").read_text())["images"]["python"]
    name = "rollout-metrics-ci-" + uuid.uuid4().hex[:12]
    api = name + "-api"
    try:
        probe = ROOT / ".github/scripts/tests/fixtures/rollout-api.py"
        subprocess.run(["docker", "run", "--detach", "--name", api, "--read-only", "--network", "none",
                        "--mount", f"type=bind,src={probe},dst=/probe.py,readonly", python_image,
                        "python3", "/probe.py"], check=True, stdout=subprocess.DEVNULL, timeout=30)
        ready = """from urllib.request import urlopen
from time import sleep
for _ in range(50):
    try:
        urlopen('http://127.0.0.1:40443/version', timeout=1).close()
        break
    except OSError:
        sleep(0.1)
else:
    raise SystemExit('Mock API did not become ready')
"""
        subprocess.run(["docker", "exec", api, "python3", "-c", ready], check=True, timeout=10)
        subprocess.run(["docker", "run", "--detach", "--name", name, "--read-only", "--network", "container:" + api,
                        "--mount", f"type=bind,src={directory},dst=/config,readonly", KSM_IMAGE,
                        "--custom-resource-state-only", "--custom-resource-state-config-file=/config/rollout-config.yaml",
                        "--kubeconfig=/config/kubeconfig"], check=True, stdout=subprocess.DEVNULL, timeout=30)
        status = subprocess.check_output(["docker", "wait", api], text=True, timeout=45).strip()
        subprocess.run(["docker", "logs", api], check=True, timeout=10)
        if status != "0":
            subprocess.run(["docker", "logs", name], timeout=10)
            raise AssertionError("Rollout metric runtime check failed")
    finally:
        subprocess.run(["docker", "rm", "--force", name, api], capture_output=True, timeout=30)


def main():
    with tempfile.TemporaryDirectory(prefix="monitoring-rules-ci-") as directory:
        directory = Path(directory)
        directory.chmod(0o755)
        for filename in ("otel-demo-rules.yaml", "otel-demo-dependency-rules.yaml"):
            rule = yaml.safe_load((ROOT / "platform/monitoring/resources" / filename).read_text())
            (directory / filename).write_text(yaml.safe_dump(rule["spec"]))
        expressions = []
        for filename in ("otel-demo-dashboard.yaml", "otel-demo-dependency-dashboard.yaml"):
            config = yaml.safe_load((ROOT / "platform/monitoring/resources" / filename).read_text())
            dashboard = json.loads(next(iter(config["data"].values())))
            for panel in dashboard["panels"]:
                for target in panel.get("targets", []):
                    expr = target["expr"].replace("$namespace", "dev").replace("$__rate_interval", "5m")
                    for variable in ("namespace", "workload", "topic", "consumergroup"):
                        expr = expr.replace("${" + variable + ":regex}", ".*")
                    expressions.append(expr)
        (directory / "dashboard-queries.yaml").write_text(yaml.safe_dump({"groups": [{
            "name": "dashboard-queries", "rules": [{"record": f"monitoring:dashboard:query{index}", "expr": expr}
                                                      for index, expr in enumerate(expressions)],
        }]}))
        tests = ROOT / ".github/scripts/tests/monitoring-rules.yaml"
        (directory / "tests.yaml").write_text(tests.read_text())
        for args in (["check", "rules", "/rules/otel-demo-rules.yaml", "/rules/otel-demo-dependency-rules.yaml", "/rules/dashboard-queries.yaml"],
                     ["test", "rules", "/rules/tests.yaml"]):
            subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only",
                            "--tmpfs", "/tmp:rw,nosuid,nodev,size=96m",
                            "--mount", f"type=bind,src={directory},dst=/rules,readonly",
                            "--workdir", "/rules", "--entrypoint", "/bin/promtool",
                            pipeline.PROMTOOL_IMAGE, *args], check=True, timeout=60)
        print("PASS: workload ownership, dependency alert scenarios and all changed dashboard expressions")
        check_rollout_metrics(directory)


if __name__ == "__main__":
    main()
