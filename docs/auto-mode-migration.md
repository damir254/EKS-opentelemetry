# Custom Auto Mode capacity

The `development` NodePool allows only `m7i-flex.large` (2 CPUs, 8 GiB RAM),
on-demand and amd64. It is capped at six instances (12 CPUs / 48 GiB).
Eligibility was verified against EC2 on 2026-10-07; eligible does not mean all
EKS/EC2 costs are free. `WhenEmptyOrUnderutilized` consolidation avoids cost-driven eviction of running workloads. Node expiration, maintenance and failure replacement remain.

Terraform creates the custom node role, its EC2 access entry and the security
group discovery tag. Kubernetes manifests select private subnets/security groups
by tags, so recreated IDs do not need Git edits. These manifests target the
repository's `otel-demo-eks` cluster name; update role/tag selectors if renaming it.

## Recreate

1. On a fresh, empty cluster, run
   `terraform apply -var='enable_metrics_server=false'` from `terraform/`.
   Built-in pools are disabled by default. The add-on cannot become healthy
   before custom capacity is configured; do not use this override on an existing
   environment because it removes the installed Metrics Server add-on.
2. Configure kubeconfig as the cluster administration role.
3. Run `bash platform/auto-mode/bootstrap.sh` from the repository root.
4. Run `terraform apply` again with the default `enable_metrics_server=true`.
   The add-on's pending pods trigger worker provisioning.
5. Install Argo CD and apply its root Application. Its `auto-mode` Application
   then reconciles the same manifests. New pending pods trigger node creation.

## Migrate an existing cluster

1. Apply Terraform with
   `-var='auto_mode_builtin_node_pools=["system","general-purpose"]'` to add
   permissions while retaining old pools.
2. Run the bootstrap script; schedule a test pod explicitly onto `development`
   and confirm an eligible node becomes Ready.
3. Cordon old nodes to stop new placements. Drain one old node at a time,
   respecting PDBs and waiting for replacement readiness. The custom pool's
   higher weight directs new capacity to eligible instances during migration.
   Keep existing monitoring PVCs and allow EBS reattachment in their original AZ.
   Demo PostgreSQL/Kafka/Valkey may restart with empty data; this was accepted.
4. Apply Terraform without the override only after workloads have migrated.
   Removing built-in pool names drains/terminates their remaining nodes.
5. Verify readiness, dashboard HTTPS access, unchanged monitoring volume IDs and
   that all worker nodes belong to `development`. Remove the test pods.

Do not force-delete blocked pods or PDBs to finish a drain. Investigate readiness,
AZ constraints and available replacement capacity instead.

The live migration on 2026-10-07 verified readiness, both HTTPS dashboard health
endpoints and unchanged monitoring volumes. EKS applies were scoped to
`module.eks` to leave unrelated RDS parameter drift untouched. Review a full
Terraform plan separately before applying other infrastructure changes.
