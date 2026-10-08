# Network policy ownership

- **Demo:** [its Helm chart](../helm/otel-demo/templates/networkpolicies.yaml) keeps
  standard Kubernetes default-deny, workload/dependency connections, and platform
  integration rules. Its existing policies are unchanged.
- **Platform:** [one policy chart](../platform/network-policies/) owns monitoring,
  Argo CD, Keycloak, External Secrets, ExternalDNS, Rollouts and Istio rules.
  Metrics Server is selected individually; `kube-system` has no namespace-wide deny.

The platform chart separates namespace creation, default-deny policies and workload
allowlists. [Values](../platform/network-policies/values.yaml) declare selectors,
required connections, actual Service port mappings and external destinations.
One connection definition generates both pod directions and exact Service routes.

Each platform workload receives one policy: standard `NetworkPolicy` when pod/IP
rules suffice, or EKS `ApplicationNetworkPolicy` when DNS destinations are needed.
EKS supports standard policy fields in the latter resource. This avoids duplicate
policies for the same workload. [AWS documentation](https://docs.aws.amazon.com/eks/latest/userguide/auto-net-pol.html).
The demo retains ownership of its policies; small Service DNS supplements preserve
Collector → Loki and meshed services → Istiod connectivity on Auto Mode.

Loki permits Grafana, Collector and Prometheus on 3100. Grafana/Argo CD/Keycloak
backend ingress permits ALB subnet addresses on their application ports. Internal
peers, scraping, replica communication, API, RDS and AWS access have explicit rules.
Platform DNS is limited to `172.20.0.10:53` TCP/UDP; Pod Identity permits
`169.254.170.23:80` only for AWS consumers. Update CIDRs when changing Terraform's
subnets or fixed Service CIDR, and add destinations when introducing integrations.

Loki remains unauthenticated internally and relies on these trusted-client rules.
Node exporters retain host networking, so pod policies cannot guarantee their
isolation; node security groups remain their boundary.
[Kubernetes documentation](https://kubernetes.io/docs/concepts/services-networking/network-policies/#networkpolicy-and-hostnetwork-pods).

## Fresh environment

Run `bash platform/auto-mode/bootstrap.sh` after Terraform/kubeconfig to enable the
EKS policy controller and custom capacity. Install Argo CD using repository values,
which disable its upstream chart policies, then apply the root Application.
The network Application creates namespaces and policies before platform workloads.
Keycloak has one policy owner here. There are no migration Jobs or cleanup scripts.

After deployment, check policies, workload readiness, SSO, Prometheus targets,
Loki ingestion, ExternalSecret readiness and Image Updater:

```bash
kubectl get networkpolicies,applicationnetworkpolicies -A
kubectl get policyendpoints -A
kubectl get pods -A
```

CI validates schemas, workload/bootstrap-hook coverage, required pod/Service routes
and denied connections. For opt-in live enforcement checks after environment creation:

```bash
python3 .github/scripts/test-platform-network.py --live
```

The smoke test uses disposable namespaces and mock services, verifies external
connectivity without credentials, and removes its test resources. Removing the
policy Application prunes its policies while retaining namespaces and workloads.
