# EKS OpenTelemetry Demo

Terraform provisions EKS and IAM/Pod Identity; Argo CD App-of-Apps manages the
application and platform Helm charts.

The existing kube-prometheus-stack includes custom Prometheus workload alerts and
the Git-provisioned **Otel Demo - Platform Health** Grafana dashboard. Rules and
dashboard live in `platform/monitoring/resources`; HPA saturation reuses the stack's
alert with a 10-minute hold. Alertmanager remains enabled without external routing.

Application request-rate, error-rate, and latency SLOs are deferred: the current
OpenTelemetry Collector exports to `debug`, with no application-metrics export to
Prometheus configured.
