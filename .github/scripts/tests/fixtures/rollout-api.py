"""Mock read-only Kubernetes discovery/list/watch for the Rollout metric check."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen


ROLLOUT = {
    "apiVersion": "argoproj.io/v1alpha1", "kind": "Rollout",
    "metadata": {"name": "payment", "namespace": "dev", "resourceVersion": "1", "uid": "payment-test",
                 "labels": {"app.kubernetes.io/name": "otel-demo", "app.kubernetes.io/instance": "otel-demo"}},
    "spec": {"replicas": 2}, "status": {"availableReplicas": 3},
}
CRD = {
    "apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
    "metadata": {"name": "rollouts.argoproj.io", "resourceVersion": "1"},
    "spec": {"group": "argoproj.io", "scope": "Namespaced",
             "names": {"kind": "Rollout", "plural": "rollouts", "singular": "rollout"},
             "versions": [{"name": "v1alpha1", "served": True, "storage": True}]},
}


class API(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        request = urlparse(self.path)
        path = request.path
        groups = [("argoproj.io", "v1alpha1"), ("apiextensions.k8s.io", "v1")]
        if path == "/version":
            body = {"major": "1", "minor": "34", "gitVersion": "v1.34.0"}
        elif path == "/api":
            body = {"kind": "APIVersions", "versions": ["v1"]}
        elif path == "/apis":
            body = {"kind": "APIGroupList", "groups": [{"name": group,
                    "versions": [{"groupVersion": group + "/" + version, "version": version}],
                    "preferredVersion": {"groupVersion": group + "/" + version, "version": version}}
                    for group, version in groups]}
        elif path in ["/apis/" + group for group, _ in groups]:
            group, version = next((g, v) for g, v in groups if path == "/apis/" + g)
            body = {"kind": "APIGroup", "name": group,
                    "versions": [{"groupVersion": group + "/" + version, "version": version}],
                    "preferredVersion": {"groupVersion": group + "/" + version, "version": version}}
        elif path == "/apis/argoproj.io/v1alpha1":
            body = {"kind": "APIResourceList", "groupVersion": "argoproj.io/v1alpha1",
                    "resources": [{"name": "rollouts", "singularName": "rollout", "namespaced": True,
                                   "kind": "Rollout", "verbs": ["list", "watch"]}]}
        elif path == "/apis/apiextensions.k8s.io/v1":
            body = {"kind": "APIResourceList", "groupVersion": "apiextensions.k8s.io/v1",
                    "resources": [{"name": "customresourcedefinitions", "namespaced": False,
                                   "kind": "CustomResourceDefinition", "verbs": ["list", "watch"]}]}
        elif path.endswith("/rollouts") or path.endswith("/customresourcedefinitions"):
            item = ROLLOUT if path.endswith("/rollouts") else CRD
            if parse_qs(request.query).get("watch") == ["true"]:
                body = {"type": "ADDED", "object": item}
            else:
                body = {"apiVersion": item["apiVersion"], "kind": item["kind"] + "List",
                        "metadata": {"resourceVersion": "1"}, "items": [item]}
        elif path == "/api/v1":
            body = {"kind": "APIResourceList", "groupVersion": "v1", "resources": []}
        else:
            self.send_error(404)
            return
        data = (json.dumps(body) + "\n").encode()
        if parse_qs(request.query).get("sendInitialEvents") == ["true"]:
            # Recent client-go reflectors need the initial-events-end bookmark
            # before exposing items received through a watch-list request.
            bookmark = {"type": "BOOKMARK", "object": {
                "apiVersion": item["apiVersion"], "kind": item["kind"],
                "metadata": {"resourceVersion": "1", "annotations": {"k8s.io/initial-events-end": "true"}},
            }}
            data += (json.dumps(bookmark) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if "watch" not in parse_qs(request.query):
            self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        if "watch" in parse_qs(request.query):
            time.sleep(35)  # Keep the watch stream open while metrics are checked.


server = ThreadingHTTPServer(("127.0.0.1", 40443), API)
threading.Thread(target=server.serve_forever, daemon=True).start()
deadline = time.monotonic() + 30
last = ""
while time.monotonic() < deadline:
    try:
        with urlopen("http://127.0.0.1:8080/metrics", timeout=2) as response:
            last = response.read().decode()
        lines = last.splitlines()
        for name, value in [("otel_demo_rollout_identity_info", 1), ("otel_demo_rollout_replicas_desired", 2),
                            ("otel_demo_rollout_replicas_available", 3)]:
            matching = [line for line in lines if line.startswith(name + "{")
                        and 'app_name="otel-demo"' in line and 'app_instance="otel-demo"' in line
                        and 'namespace="dev"' in line and 'rollout="payment"' in line]
            assert len(matching) == 1 and float(matching[0].rsplit(" ", 1)[1]) == value
        print("PASS: pinned kube-state-metrics exposes actual Rollout identity, desired and available replicas")
        break
    except (OSError, AssertionError):
        time.sleep(0.2)
else:
    raise AssertionError("Rollout metrics were not generated:\n" + last)
server.shutdown()
