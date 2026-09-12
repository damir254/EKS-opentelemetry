# EKS OpenTelemetry Demo

Terraform provisions EKS and IAM/Pod Identity; Argo CD App-of-Apps manages the
application and platform Helm charts.

The existing kube-prometheus-stack includes custom Prometheus workload alerts and
the Git-provisioned **Otel Demo - Platform Health** Grafana dashboard. Rules and
dashboard live in `platform/monitoring/resources`; HPA saturation reuses the stack's
alert with a 10-minute hold. Alertmanager remains enabled without external routing.

Application traces → OpenTelemetry Collector → `span_metrics` → Prometheus
exporter → Prometheus → Grafana / Alertmanager. The **Otel Demo - Application RED**
dashboard shows server request rate, error rate, and p95/p99 latency; native OTLP
metrics and existing debug exports remain enabled. Warning alerts cover errors
above 5% for 5 minutes and p95 above 1 second for 10 minutes, both requiring at
least 1 request/second. RED metrics describe received SERVER spans and depend on
tracing coverage, sampling, and correct error status. Metrics are scraped through
the internal Collector Service on port 8889.
