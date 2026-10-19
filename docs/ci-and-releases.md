# CI and releases

GitHub Actions validates application and platform changes. Argo CD handles
Kubernetes delivery from Git; Terraform changes are planned and applied manually.

## What CI checks

| Workflow | Purpose |
| --- | --- |
| [Infrastructure CI](../.github/workflows/ci-infrastructure.yaml) | Validate Terraform and workflows; run platform tests. |
| [Payment CI](../.github/workflows/ci-payment.yaml) | Test payment behavior and graceful shutdown, then build and scan the image. |
| [Product Catalog CI](../.github/workflows/ci-product-catalog.yaml) | Run Go tests, then build and scan the image. |
| [Recommendation CI](../.github/workflows/ci-recommendation.yaml) | Test recommendations and telemetry startup, then build and scan the image. |
| [Scheduled image scans](../.github/workflows/scan-images.yaml) | Report CVEs in configured public and private ECR images daily or on manual dispatch. |

Infrastructure CI runs on every PR and push to `main`. It checks Terraform and
workflow syntax, then tests credential initialization, release safeguards and
selected component behavior using local Docker containers.

These checks run without AWS credentials or a live cluster. They provide coverage
for configuration and component behavior, rather than a complete deployment test.

## Application image delivery

The three maintained services share [one build-and-publish workflow](../.github/workflows/reusable-build-push.yaml):

1. Service tests must pass.
2. Build one `linux/amd64` OCI image archive with an SBOM and build provenance.
3. Scan that artifact with Trivy. Fixable HIGH or CRITICAL vulnerabilities block publication.
4. On a push to `main`, upload a candidate image and check its digest against the build output.
5. Sign and verify the image with Cosign, then publish its immutable `release-<commit SHA>` tag.
6. Image Updater selects eligible release images and commits their references to
   [dev-images.yaml](../helm/otel-demo/environments/dev-images.yaml).
7. Argo CD reconciles that Git change. Payment proceeds through a canary rollout
   checked against Prometheus traffic, error-rate and latency metrics.

The artifact scanned is the artifact published; publication does not rebuild it.
The SBOM records included software, and provenance records build information.
Signature verification takes place in CI. An admission policy requiring image
signatures is not currently configured in the cluster.

PR runs build and scan without AWS publishing credentials. Main-branch publication
uses GitHub OIDC to obtain short-lived AWS credentials. Actions, base images and
tool versions are pinned in their configuration files.

The scheduled scan runs daily at 04:43 UTC and can be triggered manually from
`main`. It discovers runtime images from rendered GitOps configuration, including
init containers and controller images, using [discover-images.py](../.github/scripts/discover-images.py).
Private ECR scans reuse `AWS_ROLE_ARN` and GitHub OIDC. Reports include all CVE
severities and unfixed findings in job summaries and downloadable artifacts kept
for seven days. Findings are informational; scan/authentication errors fail the run.

## Required checks

Required status checks must be enabled through GitHub repository rules; workflow
files alone do not enforce them. Manage these settings in GitHub and preserve
Image Updater's Git write-back access when configuring branch restrictions.

For local checks, use the commands and tool versions in Infrastructure CI.
Individual service READMEs describe their test commands.
