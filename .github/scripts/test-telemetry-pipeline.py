"""Exercise Collector failure metrics and Prometheus alerts without a cluster."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# Same Prometheus version as kube-prometheus-stack 88.3.0, with an immutable
# test-tool digest so rule validation does not drift with mutable tags.
PROMTOOL_IMAGE = "quay.io/prometheus/prometheus:v3.13.2-distroless@sha256:64f71bb84e03c855948418b0fc5dea53e9543d8e3fc9931598f583805507f05e"


def probe():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class Sink(BaseHTTPRequestHandler):
        status = 400

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(type(self).status)
            self.end_headers()

        def log_message(self, *_):
            pass

    sink = ThreadingHTTPServer(("127.0.0.1", 49000), Sink)
    threading.Thread(target=sink.serve_forever, daemon=True).start()

    def emit(path, payload):
        request = Request("http://127.0.0.1:4318" + path,
                          data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status
        except HTTPError as error:
            return error.code

    def scrape(port=8888):
        with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
            return response.read().decode()

    def values(text, metric):
        return [float(match.group(1)) for line in text.splitlines()
                if (match := re.fullmatch(re.escape(metric) + r'(?:\{.*\})? ([\d.eE+-]+)', line))]

    def total(text, metric):
        return sum(values(text, metric))

    def wait_for(check, timeout=15):
        deadline = time.monotonic() + timeout
        latest = ""
        while time.monotonic() < deadline:
            try:
                latest = scrape()
                if check(latest):
                    return latest
            except OSError:
                pass
            time.sleep(0.1)
        raise AssertionError("Expected pipeline metrics missing:\n" + latest)

    resource = {"attributes": [
        {"key": "service.name", "value": {"stringValue": "pipeline-ci"}},
        {"key": "service.instance.id", "value": {"stringValue": "pipeline-ci-pod"}},
    ]}

    def log_record():
        return {"resourceLogs": [{"resource": resource, "scopeLogs": [{
            "scope": {"name": "pipeline-ci"}, "logRecords": [{
                "timeUnixNano": str(time.time_ns()), "severityNumber": 9,
                "body": {"stringValue": "local pipeline regression"},
            }],
        }]}]}

    wait_for(lambda text: bool(values(text, "otelcol_process_uptime")))
    now = time.time_ns()
    assert emit("/v1/traces", {"resourceSpans": [{"resource": resource, "scopeSpans": [{
        "scope": {"name": "pipeline-ci"}, "spans": [{
            "traceId": "0123456789abcdef0123456789abcdef", "spanId": "0123456789abcdef",
            "name": "local-request", "kind": 2, "startTimeUnixNano": str(now),
            "endTimeUnixNano": str(now + 1_000_000),
        }],
    }]}]}) == 200
    assert emit("/v1/metrics", {"resourceMetrics": [{"resource": resource, "scopeMetrics": [{
        "scope": {"name": "pipeline-ci"}, "metrics": [{
            "name": "pipeline.ci.counter", "sum": {"isMonotonic": True,
            "aggregationTemporality": 2, "dataPoints": [{"asInt": "1",
            "startTimeUnixNano": str(now), "timeUnixNano": str(now + 1_000_000)}]},
        }],
    }]}]}) == 200
    assert emit("/v1/logs", log_record()) == 200
    wait_for(lambda text: total(text, "otelcol_exporter_send_failed_log_records") > 0
             and total(text, "otelcol_receiver_accepted_spans") > 0
             and total(text, "otelcol_receiver_accepted_metric_points") > 0
             and total(text, "otelcol_receiver_accepted_log_records") > 0)

    # A retrying export occupies an in-flight request; a burst then fills the
    # deliberately small test queue. Production queue/retry settings are unchanged.
    Sink.status = 503
    for _ in range(30):
        emit("/v1/logs", log_record())
        time.sleep(0.1)
    text = wait_for(lambda text: total(text, "otelcol_exporter_enqueue_failed_log_records") > 0
                    and total(text, "otelcol_exporter_queue_size") > 0
                    and total(text, "otelcol_exporter_in_flight_requests") > 0)
    for metric in ("otelcol_exporter_queue_capacity", "otelcol_process_cpu_seconds",
                   "otelcol_process_memory_rss"):
        assert values(text, metric), metric
    assert "otel_demo_span_metrics_calls_total" in scrape(8889)

    Sink.status = 204
    emit("/v1/logs", log_record())
    wait_for(lambda text: total(text, "otelcol_exporter_sent_log_records") > 0
             and total(text, "otelcol_exporter_queue_size") == 0
             and total(text, "otelcol_exporter_in_flight_requests") == 0)
    sink.shutdown()
    print("PASS: internal metrics expose accepted telemetry, failed exports, queue pressure,")
    print("enqueue failures and in-flight retries; log delivery recovers and RED remains available.")


def run():
    import yaml

    ROOT = Path(__file__).resolve().parents[2]
    application = yaml.safe_load((ROOT / "platform/argocd/applications/otel-demo.yaml").read_text())
    chart = ROOT / application["spec"]["source"]["path"]
    command = ["helm", "template", "otel-demo", str(chart), "--namespace", "dev"]
    for filename in application["spec"]["source"]["helm"]["valueFiles"]:
        command += ["--values", str(chart / filename)]
    resources = [item for item in yaml.safe_load_all(
        subprocess.check_output(command, text=True)) if item]
    collector = next(item for item in resources if item["kind"] == "Deployment"
                     and item["metadata"]["name"] == "otel-collector")
    image = collector["spec"]["template"]["spec"]["containers"][0]["image"]
    config = yaml.safe_load(next(item for item in resources if item["kind"] == "ConfigMap"
                           and item["metadata"]["name"] == "otel-collector-config")["data"]["otel-collector-config.yaml"])
    python_image = yaml.safe_load((ROOT / "platform/monitoring/database/values.yaml").read_text())["images"]["python"]
    name = "telemetry-pipeline-ci-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="telemetry-pipeline-ci-") as directory:
        directory = Path(directory)
        directory.chmod(0o755)  # promtool runs as the image's non-root user.
        rule = yaml.safe_load((ROOT / "platform/monitoring/resources/telemetry-pipeline-rules.yaml").read_text())
        (directory / "telemetry-pipeline.yaml").write_text(yaml.safe_dump(rule["spec"]))
        (directory / "tests.yaml").write_text((ROOT / ".github/scripts/tests/telemetry-pipeline-rules.yaml").read_text())
        dashboard_config = yaml.safe_load((ROOT / "platform/monitoring/resources/telemetry-pipeline-dashboard.yaml").read_text())
        dashboard = json.loads(dashboard_config["data"]["telemetry-pipeline.json"])
        expressions = [target["expr"].replace("$namespace", "dev").replace("$__rate_interval", "5m")
                       for panel in dashboard["panels"] for target in panel["targets"]]
        (directory / "dashboard-queries.yaml").write_text(yaml.safe_dump({"groups": [{
            "name": "dashboard-queries", "rules": [{"record": f"telemetry:dashboard:query{index}", "expr": expr}
                                                      for index, expr in enumerate(expressions)],
        }]}))
        for args in (["check", "rules", "/rules/telemetry-pipeline.yaml"],
                     ["check", "rules", "/rules/dashboard-queries.yaml"],
                     ["test", "rules", "/rules/tests.yaml"]):
            subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only",
                            "--tmpfs", "/tmp:rw,nosuid,nodev,size=96m",
                            "--mount", f"type=bind,src={directory},dst=/rules,readonly",
                            "--workdir", "/rules", "--entrypoint", "/bin/promtool",
                            PROMTOOL_IMAGE, *args], check=True, timeout=60)

        # Keep the production endpoint/metric configuration and change only
        # batching and the test sink's queue/retry behavior for fault injection.
        config["processors"]["batch"]["timeout"] = "50ms"
        config["connectors"]["span_metrics"]["metrics_flush_interval"] = "1s"
        config["exporters"]["otlphttp/loki"].update({
            "endpoint": "http://127.0.0.1:49000/otlp",
            "sending_queue": {"enabled": True, "queue_size": 4, "num_consumers": 1},
            "retry_on_failure": {"enabled": True, "initial_interval": "100ms",
                                 "max_interval": "200ms", "max_elapsed_time": "1s"},
        })
        config_path = directory / "collector.yaml"
        config_path.write_text(yaml.safe_dump(config))
        mount = ["--mount", f"type=bind,src={config_path},dst=/config.yaml,readonly"]
        subprocess.run(["docker", "run", "--rm", "--network", "none", *mount,
                        image, "validate", "--config=/config.yaml"], check=True, timeout=30)
        try:
            subprocess.run(["docker", "run", "--detach", "--name", name, "--read-only",
                            "--network", "none", *mount, image, "--config=/config.yaml"],
                           check=True, stdout=subprocess.DEVNULL, timeout=30)
            subprocess.run(["docker", "run", "--rm", "--read-only", "--network", f"container:{name}",
                            "--mount", f"type=bind,src={Path(__file__).resolve()},dst=/probe.py,readonly",
                            python_image, "python3", "/probe.py", "--probe"], check=True, timeout=60)
        finally:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe"]:
        probe()
    else:
        run()
