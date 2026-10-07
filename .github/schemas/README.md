# Auto Mode schemas

The NodePool and NodeClass schemas are derived from the installed EKS Auto Mode
v1 CRDs on Kubernetes 1.36 (2026-10-07). They contain API definitions only.
`validate-kubernetes.py` converts nullable OpenAPI fields and validates manifests
strictly alongside the existing IngressClassParams schema and chart CRDs.

Refresh these schemas from the matching EKS APIs when introducing new Auto Mode
fields or upgrading the API version; do not bypass missing-schema validation.
