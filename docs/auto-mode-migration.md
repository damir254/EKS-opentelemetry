# Auto Mode capacity and cluster startup

The cluster uses a custom Auto Mode NodeClass and NodePool named `development`.
Terraform creates the AWS roles and access configuration; the Kubernetes manifests
describe which worker capacity Auto Mode can provision.

The current pool uses on-demand, amd64 `m7i-flex.large` instances in private subnets.
Its resource limits allow up to six of those instances. This is a capacity limit,
not a fixed node count: pending pods trigger provisioning, and consolidation can
remove or replace nodes when workloads fit on less capacity.

Configuration lives in [platform/auto-mode](../platform/auto-mode/).
The NodeClass uses cluster-specific role names and discovery tags, which must
match Terraform if the cluster name changes. Built-in node pools are disabled.

## Fresh cluster startup

Before starting, configure the AWS/GitHub/domain references, local Terraform inputs
and state backend. [terraform/bootstrap](../terraform/bootstrap/) creates the state
bucket separately; configure [backend.tf](../terraform/backend.tf) to use it.

Run the following sequence from the repository root:

1. Initialize Terraform and create infrastructure without Metrics Server:

   ```sh
   terraform -chdir=terraform init
   terraform -chdir=terraform apply -var='enable_metrics_server=false'
   ```

2. Authenticate as the configured cluster-administration role and update kubeconfig.
   Terraform's `update_kubeconfig_command` output provides the command.

3. Enable networking support and install the custom capacity definitions:

   ```sh
   bash platform/auto-mode/bootstrap.sh
   ```

4. Apply Terraform again with the default Metrics Server setting:

   ```sh
   terraform -chdir=terraform apply
   ```

5. Prepare application secrets, initial ECR images and Image Updater access, then
   install Argo CD using [platform/argocd/values.yaml](../platform/argocd/values.yaml).
   Use the chart version validated by Infrastructure CI and apply
   [root-application.yaml](../platform/argocd/root-application.yaml).

The initial override avoids waiting for an add-on before custom worker capacity
exists. Use it only for a fresh cluster: disabling the setting on an existing
environment removes the installed Metrics Server add-on.

After startup, Argo CD owns reconciliation of the same Auto Mode manifests.
It also deploys platform network allowlists from
[platform/network-policies](../platform/network-policies/); demo service rules are
configured in the [demo chart](../helm/otel-demo/values/network-policy.yaml).

## Availability during node replacement

Consolidation and maintenance can move workloads between nodes. Replicas,
disruption budgets and placement rules protect selected services, but
single-instance demo dependencies briefly stop while their EBS volumes reattach.
Their volumes remain tied to an availability zone.

When changing capacity, inspect pending-pod events, placement constraints and
replacement readiness. See [storage lifecycle](dependency-storage.md) for the
difference between replacing a pod and deleting its persistent data.
