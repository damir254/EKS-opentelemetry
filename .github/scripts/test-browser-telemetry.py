"""Exercise rendered browser ingestion with pinned Envoy/Collector, without AWS."""

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

import yaml


ROOT = Path(__file__).resolve().parents[2]
TRACE_PATH = "/otlp-http/v1/traces"


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(f"Command failed: {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


class BrowserTelemetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="browser-telemetry-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        application = yaml.safe_load((ROOT / "platform/argocd/applications/otel-demo.yaml").read_text())
        chart = ROOT / application["spec"]["source"]["path"]
        command = ["helm", "template", "otel-demo", str(chart), "--namespace", "dev"]
        for filename in application["spec"]["source"]["helm"]["valueFiles"]:
            command += ["--values", str(chart / filename)]
        cls.resources = list(filter(None, yaml.safe_load_all(run(*command))))

        def resource(kind, name):
            return next(item for item in cls.resources if item["kind"] == kind and item["metadata"]["name"] == name)

        cls.resource = staticmethod(resource)
        envoy_config = resource("ConfigMap", "frontend-proxy-config")["data"]["envoy.yaml"]
        collector_config = resource("ConfigMap", "otel-collector-config")["data"]["otel-collector-config.yaml"]
        envoy_file = directory / "envoy.yaml"
        collector_file = directory / "collector.yaml"
        envoy_file.write_text(envoy_config)
        collector_file.write_text(collector_config)
        envoy_image = resource("Deployment", "frontend-proxy")["spec"]["template"]["spec"]["containers"][0]["image"]
        collector_image = resource("Deployment", "otel-collector")["spec"]["template"]["spec"]["containers"][0]["image"]
        python_image = yaml.safe_load((ROOT / "platform/monitoring/database/values.yaml").read_text())["images"]["python"]

        def mount(path):
            return f"type=bind,src={path},dst=/config.yaml,readonly"

        # Validate the production configs before shortening only flush intervals.
        run("docker", "run", "--rm", "--network", "none", "--mount", mount(envoy_file),
            envoy_image, "--mode", "validate", "-c", "/config.yaml")
        run("docker", "run", "--rm", "--network", "none", "--mount", mount(collector_file),
            collector_image, "validate", "--config=/config.yaml")
        collector = yaml.safe_load(collector_config)
        collector["processors"]["batch"]["timeout"] = "100ms"
        collector["connectors"]["span_metrics"]["metrics_flush_interval"] = "1s"
        collector_file.write_text(yaml.safe_dump(collector))

        cls.network = f"browser-telemetry-{uuid.uuid4().hex[:12]}"
        cls.containers = []
        run("docker", "network", "create", "--internal", cls.network)
        cls.addClassCleanup(cls.cleanup_docker)

        def start(alias, image, ports, args=(), config=None):
            name = f"{cls.network}-{alias}"
            command = ["docker", "run", "--detach", "--name", name, "--network", cls.network,
                       "--network-alias", alias]
            if config:
                command += ["--mount", mount(config)]
            cls.containers.append(name)
            run(*command, image, *args)
            # Linux CI can reach the isolated bridge directly; publish no ports.
            inspection = json.loads(run("docker", "inspect", name))[0]
            address = inspection["NetworkSettings"]["Networks"][cls.network]["IPAddress"]
            return {port: (address, port) for port in ports}

        mock_frontend = """
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'frontend')
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.send_response(200); self.end_headers(); self.wfile.write(str(len(body)).encode())
    def log_message(self, *args):
        pass
HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
"""
        start("frontend", python_image, [], ("python3", "-c", mock_frontend))
        cls.collector_ports = start("otel-collector", collector_image, [4318, 4319, 8889, 13133],
                                    ("--config=/config.yaml",), collector_file)
        cls.proxy_ports = start("proxy", envoy_image, [8080, 10000],
                                ("-c", "/config.yaml", "--concurrency", "2"), envoy_file)
        for port, path in [(cls.collector_ports[13133], "/"), (cls.proxy_ports[10000], "/ready"),
                           (cls.proxy_ports[8080], "/")]:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    if cls.request(port, "GET", path)[0] == 200:
                        break
                except (OSError, http.client.HTTPException):
                    pass
                time.sleep(0.2)
            else:
                logs = "\n".join(run("docker", "logs", name) for name in cls.containers)
                raise RuntimeError(f"Test listener did not become ready: {port}\n{logs}")

    @classmethod
    def cleanup_docker(cls):
        # Remove only this test's uniquely named resources, including on failure.
        for name in reversed(cls.containers):
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)
        subprocess.run(["docker", "network", "rm", cls.network], capture_output=True, timeout=30)

    @staticmethod
    def request(port, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            connection.close()

    def post(self, body=b"{}", content_type="application/json", headers=None):
        return self.request(self.proxy_ports[8080], "POST", TRACE_PATH, body,
                            {"Content-Type": content_type, **(headers or {})})

    def test_01_browser_export_formats_and_fault_header(self):
        self.assertEqual(self.post()[0], 200)
        self.assertEqual(self.post(b"", "application/x-protobuf")[0], 200)
        self.assertEqual(self.post(headers={"x-envoy-fault-delay-request": "60000"})[0], 200)

    def test_02_only_uncompressed_trace_posts_are_public(self):
        for method in ["GET", "OPTIONS", "PUT"]:
            with self.subTest(method=method):
                status, headers, _ = self.request(self.proxy_ports[8080], method, TRACE_PATH,
                                                  headers={"x-envoy-fault-delay-request": "60000"})
                self.assertEqual(status, 405)
                self.assertEqual(headers.get("allow"), "POST")
        for path in ["/otlp-http", "/otlp-http/v1/logs", "/otlp-http/v1/metrics", "/otlp-http/other"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(self.proxy_ports[8080], "POST", path, b"{}",
                                              {"x-envoy-fault-delay-request": "60000"})[0], 404)
        self.assertEqual(self.post(content_type="text/plain",
                                   headers={"x-envoy-fault-delay-request": "60000"})[0], 415)
        self.assertEqual(self.post(headers={"Content-Encoding": "gzip"})[0], 415)
        for path in ["/v1/logs", "/v1/metrics"]:
            with self.subTest(browser_receiver=path):
                self.assertEqual(self.request(self.collector_ports[4319], "POST", path, b"{}",
                                              {"Content-Type": "application/json"})[0], 404)

    def test_03_body_limit_does_not_limit_application_routes(self):
        maximum = 262144
        self.assertEqual(self.post(b"{}" + b" " * (maximum - 2))[0], 200)
        self.assertEqual(self.post(b"{}" + b" " * (maximum - 1))[0], 413)
        status, _, body = self.request(self.collector_ports[4319], "POST", "/v1/traces",
                                      b"{}" + b" " * maximum, {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIn("request body too large", body)
        body = b"x" * (maximum + 1)
        status, _, received = self.request(self.proxy_ports[8080], "POST", "/api/example", body)
        self.assertEqual(status, 200)
        self.assertEqual(received, str(len(body)))

    def test_04_browser_identity_isolated_from_backend_metrics(self):
        def trace(service, identity):
            attributes = [{"key": key, "value": {"stringValue": value}} for key, value in
                          [("service.name", service), ("service.namespace", "dev"),
                           ("rollout.pod_template_hash", identity), ("service.version", identity)]]
            start = time.time_ns()
            return json.dumps({"resourceSpans": [{"resource": {"attributes": attributes}, "scopeSpans": [{
                "scope": {"name": "browser-security-test"}, "spans": [{
                    "traceId": uuid.uuid4().hex, "spanId": uuid.uuid4().hex[:16], "name": "test",
                    "kind": 2, "startTimeUnixNano": str(start), "endTimeUnixNano": str(start + 1000000),
                    "attributes": attributes, "status": {"code": 1}}]}]}]}).encode()

        self.assertEqual(self.post(trace("payment", "forged-browser-identity"))[0], 200)
        self.assertEqual(self.request(self.collector_ports[4318], "POST", "/v1/traces",
                                     trace("payment", "trusted-backend"), {"Content-Type": "application/json"})[0], 200)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            metrics = self.request(self.collector_ports[8889], "GET", "/metrics")[2]
            if 'service_name="frontend-web"' in metrics and 'service_name="payment"' in metrics:
                break
            time.sleep(0.2)
        self.assertIn('service_name="frontend-web"', metrics)
        self.assertIn('service_name="payment"', metrics)
        self.assertIn('rollout_pod_template_hash="trusted-backend"', metrics)
        self.assertNotIn("forged-browser-identity", metrics)
        browser_calls = [line for line in metrics.splitlines() if 'service_name="frontend-web"' in line]
        self.assertTrue(browser_calls)
        self.assertTrue(all('rollout_pod_template_hash=' not in line and 'service_version=' not in line
                            for line in browser_calls))

    def test_05_rendered_service_and_policies_isolate_browser_port(self):
        service = self.resource("Service", "otel-collector")["spec"]
        self.assertIn({"name": "http-browser", "port": 4319, "targetPort": "http-browser"}, service["ports"])
        egress = self.resource("NetworkPolicy", "otel-demo-frontend-proxy")["spec"]["egress"]
        ingress = self.resource("NetworkPolicy", "otel-demo-otel-collector")["spec"]["ingress"]
        for rules, direction, component in [(egress, "to", "otel-collector"), (ingress, "from", "frontend-proxy")]:
            allowed = {port["port"] for rule in rules for port in rule.get("ports", [])
                       if any(peer.get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/component")
                              == component for peer in rule.get(direction, []))}
            self.assertEqual(allowed, {4317, 4319})
        frontend = self.resource("Deployment", "frontend")["spec"]["template"]["spec"]["containers"][0]
        endpoint = next(env["value"] for env in frontend["env"]
                        if env["name"] == "PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        self.assertEqual(endpoint, TRACE_PATH)

    def test_06_rate_limit_keeps_application_routes_available(self):
        time.sleep(2)  # Allow the configured burst bucket to refill after other tests.
        with ThreadPoolExecutor(max_workers=20) as executor:
            statuses = list(executor.map(lambda _: self.post()[0], range(80)))
        self.assertIn(200, statuses)
        self.assertIn(429, statuses)
        self.assertTrue(set(statuses).issubset({200, 429}), statuses)
        self.assertEqual(self.request(self.proxy_ports[8080], "GET", "/")[0], 200)

    def test_07_load_generator_ui_and_control_paths_are_not_public(self):
        for method, path in [("GET", "/loadgen"), ("GET", "/loadgen/"),
                             ("POST", "/loadgen/swarm"), ("POST", "/loadgen/stop")]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(self.proxy_ports[8080], method, path,
                                              headers={"x-envoy-fault-delay-request": "60000"})[0], 404)

    def test_08_load_generation_uses_internal_target_and_separate_alb_access(self):
        self.assertEqual(self.resource("Service", "load-generator")["spec"]["type"], "ClusterIP")
        loadgen = self.resource("Deployment", "load-generator")["spec"]["template"]["spec"]["containers"][0]
        target = next(env["value"] for env in loadgen["env"] if env["name"] == "LOCUST_HOST")
        self.assertEqual(target, "http://frontend-proxy:8080")
        self.assertEqual(self.resource("NetworkPolicy", "otel-demo-load-generator")["spec"]["ingress"], [])
        alb = self.resource("NetworkPolicy", "otel-demo-allow-alb-load-generator")["spec"]
        self.assertEqual(alb["podSelector"]["matchLabels"]["app.kubernetes.io/component"], "load-generator")
        self.assertEqual(alb["ingress"], [{"from": [{"ipBlock": {"cidr": "10.0.0.0/16"}}],
                                           "ports": [{"protocol": "TCP", "port": 8089}]}])
        egress = self.resource("NetworkPolicy", "otel-demo-frontend-proxy")["spec"]["egress"]
        self.assertFalse(any(peer.get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/component")
                             == "load-generator" for rule in egress for peer in rule.get("to", [])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
