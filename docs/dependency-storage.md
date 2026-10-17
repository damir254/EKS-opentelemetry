# Storage and environment lifecycle

The project separates disposable demo data from platform state.

| Component | Storage | Lifecycle |
| --- | --- | --- |
| Demo PostgreSQL | One replica, 5 GiB gp3 EBS | Survives pod replacement; deleted with its StatefulSet. |
| Kafka | One replica, 10 GiB gp3 EBS | Persists messages and broker metadata; deleted with its StatefulSet. |
| Valkey | One replica, 1 GiB gp3 EBS | Persists carts using AOF; deleted with its StatefulSet. |
| Prometheus / Alertmanager | EBS PVCs per replica | Configured to retain PVCs when removed or scaled down. |
| Loki | One replica with an EBS PVC | Configured to retain its PVC when removed or scaled down. |
| Grafana / Keycloak | Separate databases on shared RDS PostgreSQL | Database contents can be recovered from retained RDS snapshots. |

The demo dependencies have persistence, but no application-level replication.
A node replacement can interrupt them while EBS reattaches in the same availability
zone. PostgreSQL initializes schemas/products on an empty volume; Kafka keeps its
logs and metadata on the volume; Valkey uses AOF with one-second synchronization.

## Credentials and database recovery

Grafana and Keycloak credentials are stored in separate Secrets Manager bundles.
Bootstrap Jobs configure database access and reuse existing credentials when those
bundles exist. External Secrets supplies the corresponding Kubernetes Secrets.

Deleting RDS alone can leave the application secrets available for recreation.
A full Terraform destroy also includes those Secrets Manager resources. The
configured recovery window determines whether deletion is recoverable; setting
`secret_recovery_window_in_days = 0` requests deletion without a recovery window.

Keeping a credential bundle does not preserve database contents. To retain users,
dashboards and identity settings across recreation, preserve both the RDS data
and the application secrets, including Grafana's encryption key. The RDS
administrator secret is managed separately by AWS and is deleted with the instance.

RDS is configured to take a final snapshot on deletion. The recovery helper can
also create a verified manual snapshot beforehand:

```sh
python3 .github/scripts/database-recovery.py snapshot
```

After the old instance has been removed, this command selects a suitable retained
snapshot and writes local Terraform restore inputs:

```sh
python3 .github/scripts/database-recovery.py restore
```

Review and apply Terraform separately to perform the restore. Deletion protection
and local overrides also affect teardown; review the effective Terraform plan.

## Demo storage cleanup before cluster destruction

The demo StatefulSets delete their PVCs when removed. The `gp3` StorageClass then
deletes the backing EBS volumes. Stop reconciliation of both the parent and demo
Applications before removing the StatefulSets:

```sh
kubectl patch application platform-root -n argocd --type=merge \
  -p '{"spec":{"syncPolicy":{"automated":{"enabled":false}}}}'
kubectl patch application otel-demo -n argocd --type=merge \
  -p '{"spec":{"syncPolicy":{"automated":{"enabled":false}}}}'
kubectl delete statefulset astronomy-db kafka valkey-cart -n dev --wait=true
kubectl wait --for=delete pvc/data-astronomy-db-0 pvc/data-kafka-0 pvc/data-valkey-cart-0 \
  -n dev --timeout=300s
```

This intentionally deletes demo data. Confirm the associated PVs/EBS volumes are
gone while EKS and its storage controllers still exist; deleting the cluster alone
does not guarantee cleanup. Monitoring PVCs and RDS have separate lifecycles and
must be retained or cleaned up deliberately.
