#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

# Run after Terraform and kubeconfig, before Helm installs Argo CD. Auto Mode's
# controllers run outside the worker nodes, so no existing worker is required.
kubectl apply -f "$SCRIPT_DIR/network-policy-config.yaml"
kubectl apply -f "$SCRIPT_DIR/nodeclass.yaml"
kubectl wait --for=condition=Ready nodeclass/development --timeout=180s
kubectl apply -f "$SCRIPT_DIR/nodepool.yaml"
kubectl wait --for=condition=Ready nodepool/development --timeout=180s
