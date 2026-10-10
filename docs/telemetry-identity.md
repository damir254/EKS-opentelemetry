# Application telemetry identity

Every demo application workload and Flagd receives the pod's UID, name, namespace
and node through Kubernetes Downward API environment variables in
`helm/otel-demo/values.yaml`. The workload template appends these resource attributes
after merging service configuration:

```text
service.instance.id=$(K8S_POD_UID)
k8s.pod.uid=$(K8S_POD_UID)
k8s.pod.name=$(K8S_POD_NAME)
k8s.namespace.name=$(K8S_NAMESPACE_NAME)
k8s.node.name=$(K8S_NODE_NAME)
```

`K8S_*` variables precede `OTEL_RESOURCE_ATTRIBUTES` so Kubernetes expands their
values before the SDK starts. Service-specific attributes remain intact.
`OTEL_RESOURCE_ATTRIBUTES_EXTRA` is a chart input merged into the standard SDK
variable, including for Deployments. Payment retains its rollout hash and image
version. These attributes add no Kubernetes pod labels or API permissions.

The Collector's Prometheus exporter maps `service.instance.id` to `instance`,
separating native counters and histograms from different replicas. With the current
ServiceMonitor's `honorLabels: false`, Prometheus stores this as `exported_instance`;
`instance` continues to identify the Collector scrape target. Application service
identity similarly appears in `exported_job`.

Other resource attributes remain in `target_info`, keeping ordinary metric labels
bounded. Pod names and namespaces can be joined using `exported_job` and
`exported_instance`. For example, the total recommendations produced per second:

```promql
sum(rate(demo_recommendation_requests_recommendations_total{
  project="otel-demo", namespace="dev"
}[5m]))
```

Compute rates per series before summing so pod restarts and counter resets are
handled independently. RED rules continue to aggregate request/error/duration
metrics produced by the span-metrics connector at service level.

Infrastructure CI guards field references, environment ordering and attribute
merging. `python .github/scripts/test-native-metrics.py` uses the rendered
configuration and pinned Collector to check separate replica counters/histograms,
preserved Kubernetes metadata and RED totals on an isolated local Docker network.
Applying the updated Helm manifests rolls existing application pods to receive
the new environment; image rebuilds are unnecessary for this configuration change.
