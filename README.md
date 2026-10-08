![Amazon EKS platform architecture: GitOps delivery, Auto Mode workloads, HTTPS dashboards and persistent monitoring](docs/architecture.png)

The diagram shows the optional Multi-AZ database; the current Free plan configuration uses Single-AZ RDS.

# Amazon EKS Platform with Terraform and GitOps

An AWS platform for running the OpenTelemetry demo and practicing automated
delivery, progressive rollouts and observability. The configuration targets
`eu-central-1` across two availability zones.

- **Infrastructure:** Terraform provisions a VPC, private EKS Auto Mode nodes,
  NAT gateways, ECR repositories, IAM and Secrets Manager. Terraform state uses
  encrypted, versioned S3 storage with locking. Auto Mode manages compute,
  networking, load balancing and EBS provisioning.
  A custom pool uses eligible `m7i-flex.large` instances and consolidates empty
  or underutilized nodes; built-in pools are disabled.
- **Delivery:** Argo CD reconciles the platform and demo from Git. GitHub Actions
  tests services, scans one image build, then publishes and signs that artifact.
  Image Updater writes successful release tags back to Git. Infrastructure CI
  validates Terraform, Helm manifests and workflows.
- **Application:** The demo combines gRPC services, Kafka order events,
  PostgreSQL product/order data, Valkey carts and feature flags. Payment uses
  Argo Rollouts and Istio for canary releases checked by Prometheus.
  PostgreSQL, Kafka and Valkey run as single-replica StatefulSets with gp3 EBS
  storage that survives pod replacement and is deleted with the StatefulSets.
- **Observability:** OpenTelemetry feeds Prometheus metrics and Loki logs;
  Grafana provides dashboards and alerts. Monitoring data uses encrypted EBS
  volumes. Two Grafana replicas share a private Single-AZ RDS PostgreSQL database
  for settings, users and dashboards.
- **Identity:** Keycloak supplies OIDC login and MFA for Grafana and Argo CD.
  Two Keycloak replicas use a separate database and login on the existing RDS
  instance. Realm/client configuration is reconciled from Git; credentials stay
  in Secrets Manager. Encrypted snapshots preserve identity and Grafana settings
  across environment teardown and recreation.
- **Access and availability:** One HTTPS ALB serves Grafana, Argo CD, Keycloak, the demo
  and Locust, with ACM certificates and Route 53 records managed by ExternalDNS.
  The demo uses `https://demo.damircloud.com`; Locust uses
  `https://loadgen.damircloud.com`. Dashboard and Locust listener rules restrict
  access to the operator CIDR; the demo is public. Browser trace ingestion has
  request-size and rate limits; the demo no longer exposes `/loadgen/`.
  Selected workloads use replicas and disruption budgets. Pod Identity and
  External Secrets supply AWS access and credentials outside Git.
  [Platform network allowlists](docs/platform-network-isolation.md) restrict
  monitoring, identity and controllers; the demo retains its own default-deny rules.

| Directory | Contents |
| --- | --- |
| [terraform/](terraform/) | AWS infrastructure and state bootstrap |
| [platform/](platform/) | GitOps applications and platform configuration |
| [helm/otel-demo/](helm/otel-demo/) | Demo workloads, dependencies and environment values |
| [src/](src/) | Maintained Payment, Product Catalog and Recommendation services |
| [.github/](.github/) | CI, validation and release automation |

To bootstrap a fresh cluster, configure AWS/GitHub inputs and apply Terraform
with `-var='enable_metrics_server=false'`. Populate the demo secrets and initial
images, configure kubeconfig and run `bash platform/auto-mode/bootstrap.sh`.
Apply Terraform again with the default add-on setting. Then install Argo CD with
the repository values and apply its root Application. GitOps initializes
Grafana and Keycloak database logins and deploys the remaining platform.

Grafana RDS defaults to `db.t3.micro`, 20 GiB of `gp2` storage with autoscaling
disabled, and one day of automatic backups. Single-AZ database outages can affect
Grafana and Keycloak. Use bounded connection pools and monitor database capacity;
Multi-AZ remains an optional Paid plan availability upgrade.

This is a development/portfolio environment. Node consolidation can briefly interrupt
the single-instance demo dependencies; their data is disposable at environment teardown.
See [dependency storage](docs/dependency-storage.md) for migration and cleanup.
Traces currently produce metrics/debug output without a searchable trace backend.
