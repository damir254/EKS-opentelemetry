# EKS OpenTelemetry demo

Payment uses Argo Rollouts and Istio sidecars for internal weighted canary
routing through the existing `payment:8080` ClusterIP Service. Only payment and
its direct caller, checkout, join the mesh. Traffic progresses through
10% → 25% → 50% → 100%, with Prometheus analysis of canary-specific OpenTelemetry
RED metrics: traffic ≥ 0.01 requests/s, errors ≤ 5%, and p95 latency ≤ 1 second.
Each stage warms up for 60 seconds and takes three measurements 30 seconds apart,
tolerating one failed measurement per metric. Healthy versions promote
automatically; sustained failures abort and restore stable traffic.

The App-of-Apps installs the Rollouts controller and Istio base/istiod before
the application. The first Deployment-to-Rollout sync establishes a stable
baseline; subsequent immutable payment image SHA updates from Image Updater's
existing Git write-back flow start canaries. An abort restores traffic but does
not revert Git: restore the known-good image tag in Git before retrying a failed
release. Payment has no external endpoint or ingress gateway.
