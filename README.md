# OpenTelemetry Demo on Amazon EKS

A portfolio project that runs the OpenTelemetry demo shop on AWS Kubernetes.
It demonstrates infrastructure as code, automated delivery, gradual releases,
and application monitoring in one environment.

The shop supports product browsing, carts, and checkout. Payment (Node.js),
Product Catalog (Go), and Recommendation (Python) are maintained here; the other
application services use upstream demo images.

```text
Customer → AWS Application Load Balancer → Storefront → Application services

Code → GitHub Actions → ECR → Image tag update in Git → Argo CD → EKS

Application telemetry → OpenTelemetry Collector → Prometheus / Loki → Grafana
```

- **Infrastructure:** Terraform creates the VPC, standard EKS managed node
  groups in private subnets across two availability zones, ECR registries,
  IAM roles, and managed add-ons.
- **Delivery:** GitHub Actions tests the three maintained services, scans images
  with Trivy, and publishes signed images tagged with the commit SHA. Argo CD
  Image Updater updates Git; Argo CD synchronizes the application and platform
  through an App-of-Apps setup.
- **Canary releases:** Argo Rollouts and Istio gradually shift Payment traffic
  to new versions. Prometheus checks request volume, errors, and latency;
  failed analysis aborts the rollout.
- **Security:** AWS workloads use EKS Pod Identity; CI uses GitHub OIDC.
  External Secrets reads AWS Secrets Manager. Default-deny NetworkPolicies
  restrict communication between workloads.
- **Observability:** Grafana provides Application RED (request rate, errors,
  duration) and Platform Health dashboards. Application logs reach Loki through
  the Collector. In Grafana Explore, query `{service_name="checkout"}` or
  `{service_name="payment"}`.
- **Storage:** The EBS CSI managed add-on provisions encrypted volumes using the
  default gp3 StorageClass. Loki runs one monolithic replica with a 5Gi volume
  and 72-hour retention.

| Path | Purpose |
| --- | --- |
| [terraform/](terraform/) | AWS infrastructure and identities |
| [helm/otel-demo/](helm/otel-demo/) | Application workloads in the `dev` namespace |
| [platform/](platform/) | GitOps, controllers, storage, and the `monitoring` stack |
| [src/](src/) | The three maintained application services |
| [.github/workflows/](.github/workflows/) | Tests, image scanning, builds, and publishing |

To recreate it, configure your AWS and repository settings,
[bootstrap the Terraform backend](terraform/bootstrap/README.md), and provision
AWS resources. Populate secrets and initial application images, then install
Argo CD and register the
[root Application](platform/argocd/root-application.yaml).
Terraform owns AWS resources; Argo CD owns Kubernetes configuration.

This is a development environment: traces feed service metrics but have no
storage backend, and Loki has no redundancy or backups. Delete workload PVCs
while the EBS CSI driver is running before destroying the cluster. Production
would need durable log object storage and appropriate availability guarantees.
