# EKS API schemas

NodePool, NodeClass and ApplicationNetworkPolicy schemas are derived from installed
EKS Auto Mode CRDs on Kubernetes 1.36 (2026-10-07/08). They contain API definitions
only. `validate-kubernetes.py` converts nullable OpenAPI fields and validates
manifests strictly alongside IngressClassParams and chart CRDs.

Refresh schemas from matching EKS APIs when introducing new fields or upgrading
API versions; do not bypass missing-schema validation.
