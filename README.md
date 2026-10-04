# Amazon EKS Platform with Terraform and GitOps

An AWS Kubernetes platform built with Terraform and operated through GitOps.
The project covers VPC networking, EKS cluster provisioning, workload identity,
automated delivery, canary releases, and centralized monitoring.

The OpenTelemetry demo provides a distributed workload for exercising the
platform under traffic. Terraform provisions AWS infrastructure with EKS Auto
Mode; Argo CD keeps platform configuration, application controllers, monitoring,
and workloads in sync with Git.

![AWS architecture showing Terraform provisioning, GitOps delivery to EKS in private subnets across two availability zones, and the OpenTelemetry monitoring stack](docs/architecture.png)

- **AWS infrastructure:** Terraform modules provision a VPC with public and
  private subnets across two availability zones, NAT gateways, an EKS Auto Mode
  cluster, ECR repositories, IAM roles, and Secrets Manager resources. AWS manages
  node provisioning, networking, DNS, Pod Identity, load balancing, and EBS
  storage. Metrics Server remains an EKS add-on for workload HPAs.
  Terraform state uses an encrypted, versioned S3 backend with state locking.
- **Capacity:** The [EKS module](terraform/modules/eks/main.tf) enables Auto
  Mode's built-in `system` and `general-purpose` node pools. AWS sizes nodes
  according to pending pods and consolidates unused capacity. Serving services
  start with two replicas, disruption budgets, and hostname spread requiring
  two eligible nodes; frontend/payment HPAs can scale to five pods. The built-in
  pools have no project-defined node count or CPU/memory cap; the old
  two-to-five-node bounds no longer apply.
- **Networking and access:** Worker nodes run in private subnets. Auto Mode
  exposes the demo through an Application Load Balancer using the configured
  [IngressClass](platform/auto-mode/alb-ingressclass.yaml). The
  [network policy configuration](platform/auto-mode/network-policy-config.yaml)
  enables AWS's managed controller; default-deny NetworkPolicies allow defined
  workload connections and DNS on port 53. EKS Pod Identity grants AWS
  permissions to External Secrets and Argo CD Image Updater; External Secrets
  synchronizes values from AWS Secrets Manager into Kubernetes.
- **GitOps operations:** Argo CD uses an App-of-Apps setup to manage platform
  components and the demo Helm chart. Automated synchronization, pruning, and
  self-healing reconcile the cluster with the configuration in Git.
- **CI and image delivery:** GitHub Actions runs service tests and Trivy image
  scans, then publishes images to ECR with immutable commit-SHA tags, SBOMs,
  and build provenance. Cosign signs and verifies the published image digests.
  CI authenticates to AWS through GitHub OIDC; Argo CD Image Updater writes new
  image tags back to Helm values in Git.
- **Progressive delivery:** Argo Rollouts and Istio shift traffic through
  10%, 25%, and 50% canary stages before full promotion. Prometheus analysis
  checks canary request volume, error rate, and p95 latency, aborting a rollout
  when analysis fails. The Payment workload exercises this release strategy.
- **Observability:** The OpenTelemetry Collector exposes application metrics
  for Prometheus and forwards logs to Loki. Grafana provides Platform Health
  and Application RED (request rate, errors, duration) dashboards, with alert
  rules for unavailable deployments, pod restarts, errors, and latency.
- **Persistent storage:** Auto Mode provisions encrypted volumes through the
  default [gp3 StorageClass](platform/storage/gp3-storageclass.yaml), with volume
  expansion enabled. Loki uses a 5Gi volume and retains logs for 72 hours.

| Path | Purpose |
| --- | --- |
| [terraform/](terraform/) | AWS networking, EKS, IAM, registries, secrets, and state bootstrap |
| [platform/](platform/) | Argo CD applications, cluster controllers, storage, and monitoring |
| [helm/otel-demo/](helm/otel-demo/) | Per-service values, environment overrides, network policies, and canary configuration in `dev` |
| [.github/workflows/](.github/workflows/) | Service tests, image scanning, ECR publishing, and signing |
| [src/](src/) | Payment, Product Catalog, and Recommendation sources; other services use upstream images |

To deploy from scratch:

1. Adapt AWS account, IAM role, and repository settings in Terraform, platform
   manifests, Helm values, and GitHub Actions repository variables. The IAM
   principal running Terraform needs permission to provision EKS Auto Mode and
   pass the cluster/node roles. Use `cluster_admin_role_arn` for Kubernetes
   administration when bootstrapping Argo CD.
2. [Bootstrap the Terraform backend](terraform/bootstrap/), then plan
   and apply the main [Terraform configuration](terraform/). The EKS module
   enables managed compute, load balancing, and block storage together, grants
   the required cluster/node IAM policies, and installs Metrics Server. Auto
   Mode provisions nodes as pods need them.
3. Populate Secrets Manager values, publish the initial application images to
   ECR, and set their commit-SHA tags in the [development image values](helm/otel-demo/environments/dev-images.yaml).
4. Install Argo CD using the [repository values](platform/argocd/values.yaml)
   and register the [root Application](platform/argocd/root-application.yaml)
   to synchronize the platform and demo workloads. The `auto-mode` Application
   applies the network-policy ConfigMap and ALB classes before the demo; the
   `storage` Application supplies the default StorageClass before Loki.
