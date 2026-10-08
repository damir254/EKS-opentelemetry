# Demo dependency storage

PostgreSQL (`astronomy-db`), Kafka and Valkey each run one StatefulSet replica with
an encrypted gp3 EBS PVC: 5 GiB, 10 GiB and 1 GiB respectively. Existing Service
names and application connection settings stay the same.

- PostgreSQL stores its database/WAL in a `pgdata` subdirectory and initializes
  the demo schemas/products only on an empty volume.
- Kafka stores broker logs, consumer offsets and KRaft metadata on its PVC.
- Valkey persists carts through AOF with `appendfsync everysec`; an abrupt failure
  can lose approximately the latest second of writes.
- `do-not-disrupt` is removed. Consolidation can evict these pods; replacement
  pods reattach their volumes in the same availability zone. Expect an outage
  while scheduling and volume attachment complete. There is no replication or
  singleton PDB blocking eviction.

## First deployment

Commit/push the changes and let Argo CD sync `otel-demo`. This migration creates
fresh databases, Kafka state and carts; it does not copy the old ephemeral data.
The Services select only new StatefulSet pods, and Argo CD's `PruneLast` removes
the old Deployments after the replacement workloads are healthy. A brief outage
and errors from existing connections to old pods are expected.

```sh
kubectl get statefulsets,pvc -n dev
kubectl rollout status statefulset/astronomy-db -n dev
kubectl rollout status statefulset/kafka -n dev
kubectl rollout status statefulset/valkey-cart -n dev
kubectl get deployments -n dev  # The three old dependency Deployments should be gone.
```

## Storage lifecycle

The [StatefulSet PVC policy](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#persistentvolumeclaim-retention)
uses `whenDeleted: Delete`, `whenScaled: Retain`. Pod replacement and temporary
scale-down preserve data. Deleting a StatefulSet also deletes its PVC; the
[gp3 StorageClass](../platform/storage/gp3-storageclass.yaml) uses
`reclaimPolicy: Delete`, so CSI deletes the backing EBS volume after detachment.

For environment teardown, stop GitOps reconciliation first (including the parent
Application, which otherwise restores the child), then delete these StatefulSets
before destroying EKS. Wait for their PVCs/PVs and EBS volumes to disappear while
the cluster and storage controllers still exist. Removing only the EKS cluster
does not guarantee volume cleanup.

```sh
kubectl patch application platform-root -n argocd --type=merge \
  -p '{"spec":{"syncPolicy":{"automated":{"enabled":false}}}}'
kubectl patch application otel-demo -n argocd --type=merge \
  -p '{"spec":{"syncPolicy":{"automated":{"enabled":false}}}}'
kubectl delete statefulset astronomy-db kafka valkey-cart -n dev --wait=true
kubectl wait --for=delete pvc/data-astronomy-db-0 pvc/data-kafka-0 pvc/data-valkey-cart-0 \
  -n dev --timeout=300s
kubectl get pv  # Confirm their PVs are removed before Terraform destroys EKS.
```

Delete normally, without orphaning pods. Deleting a PVC loses that dependency's
data permanently. Grafana/Keycloak RDS snapshots and monitoring PVCs have their
own lifecycle and are unaffected by this change.
