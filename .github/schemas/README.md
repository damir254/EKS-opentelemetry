# Kubernetes validation schemas

These JSON schemas let [the Kubernetes validator](../scripts/validate-kubernetes.py)
check resources whose definitions are not available from the standard Kubernetes
schema registry.

They cover EKS Auto Mode NodeClass and IngressClassParams, Karpenter NodePool, and
AWS ApplicationNetworkPolicy resources. Custom resources supplied by Helm charts
are validated using schemas extracted from their rendered CRDs.

The checked-in definitions were taken from the project's EKS 1.36 APIs. Review
them when upgrading those APIs or introducing new fields so CI validates the
intended resource version. Schema validation checks fields and types; it does
not establish that the live controllers can successfully provision a resource.
