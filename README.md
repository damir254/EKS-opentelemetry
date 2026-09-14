# EKS OpenTelemetry demo

## Application logging

Application OTLP logs → the existing OpenTelemetry Collector → Loki → Grafana.
Argo CD manages Loki in the existing `monitoring` namespace using the
[Grafana Community chart](https://github.com/grafana-community/helm-charts/tree/main/charts/loki),
pinned to **18.13.0** (Loki **3.7.7**).

Loki runs as one lightweight monolithic replica with filesystem-backed TSDB v13
storage, a **5Gi PVC**, and **72-hour retention** via its built-in compactor.
Requests are **100m CPU / 256Mi memory**, with a **512Mi memory limit** and no CPU
limit. The ClusterIP endpoint is `http://loki.monitoring.svc.cluster.local:3100`;
the Collector uses `/otlp` for native OTLP HTTP ingestion. Only its logs pipeline
switches from `debug` to `otlphttp/loki`; metrics and traces keep their existing
pipelines. A narrow NetworkPolicy permits Collector egress to Loki on TCP 3100.

The existing Grafana automatically provisions an additional **Loki** datasource;
Prometheus stays the default. In **Grafana Explore**, select **Loki** and query:

```logql
{service_name="checkout"}
```

```logql
{service_name="payment"}
```

Native ingestion normalizes `service.name` to `service_name`. Expand log entries
to inspect timestamps, log bodies, severity, and the OTel/Kubernetes attributes
actually supplied by applications. Following [Loki's OTLP guidance](https://grafana.com/docs/loki/latest/send-data/otel/),
pod names and service instance IDs remain structured metadata; other native
mappings are preserved. No trace IDs, pod UIDs, request IDs, or rollout hashes are
added as index labels. This collects application OTLP logs, not container stdout.

This is intentionally a development/portfolio deployment, without HA or backups.
The PVC is deleted when the Loki StatefulSet is deleted or scaled down;
underlying PV cleanup follows the StorageClass reclaim policy.
Retention is asynchronous, and 5Gi can fill before
72 hours under heavy traffic. Logs queued only in Collector memory can be lost
during restarts or prolonged Loki outages. A production evolution would use
durable object storage such as S3 and an HA/scalable deployment where required.

## Persistent storage

Terraform installs the Amazon EBS CSI Driver as an EKS managed add-on, using the
existing Kubernetes-compatible version resolver and a dedicated EKS Pod Identity
role for `kube-system/ebs-csi-controller-sa`. Argo CD installs the default **gp3**
StorageClass before Loki. It dynamically provisions encrypted EBS volumes through
`ebs.csi.aws.com`, using the standard AWS-managed EBS key. `WaitForFirstConsumer`
selects the volume's availability zone after pod scheduling; expansion is enabled
and `reclaimPolicy: Delete` removes the volume when its PVC is deleted. Delete
workload PVCs while the CSI driver is still running, before destroying the cluster.
Loki's 5Gi PVC inherits this platform default without specifying a StorageClass.

The existing `gp2` class is left untouched. If it is also marked default,
Kubernetes chooses the most recently created default class (`gp3`) for new PVCs.
An existing PVC already assigned `gp2` will not switch classes automatically;
this change does not migrate existing volumes.
