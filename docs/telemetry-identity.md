# Application telemetry identity

Multiple replicas of the same service need distinct telemetry identities.
Otherwise their counters and histograms can overlap, making rates and latency
calculations misleading.

The demo chart supplies each application and Flagd with its pod UID, name,
namespace and node through the Kubernetes Downward API. The pod UID becomes
`service.instance.id`, while the service name identifies the application.
The shared workload template preserves this identity when service and environment
values are merged.

## Where telemetry goes

Applications export traces, metrics and logs to the OpenTelemetry Collector.
Prometheus scrapes the exported application metrics, and Loki stores the logs.
The Collector also derives request, error and duration metrics from spans for
the RED dashboards and payment canary analysis. There is currently no searchable
trace-storage backend.

Browser traces use a separate receiver with request limits and a fixed browser
identity so public input cannot impersonate backend service or rollout identity.

## Reading metrics

The Collector exports application instance identity to Prometheus. Because
Prometheus keeps its own scrape-target labels, application identity appears as
`exported_job` and `exported_instance`. Additional Kubernetes metadata is available
through `target_info`.

Calculate rates for individual series before summing across replicas. For example:

```promql
sum(rate(demo_recommendation_requests_recommendations_total{
  project="otel-demo", namespace="dev"
}[5m]))
```

The [shared chart defaults](../helm/otel-demo/values.yaml) and
[workload template](../helm/otel-demo/templates/_services.tpl) define the application
identity. The [Collector configuration](../helm/otel-demo/files/otel-collector/config.yaml)
defines signal routing.
