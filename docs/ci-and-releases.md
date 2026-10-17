# CI and releases

GitHub Actions validates application and platform changes. Argo CD handles
Kubernetes delivery from Git; Terraform changes are planned and applied manually.

## What CI checks

| Workflow | Purpose |
| --- | --- |
| [Infrastructure CI](../.github/workflows/ci-infrastructure.yaml) | Validate Terraform, workflows and rendered Kubernetes configuration; run platform tests. |
| [Payment CI](../.github/workflows/ci-payment.yaml) | Test payment behavior and graceful shutdown, then build and scan the image. |
| [Product Catalog CI](../.github/workflows/ci-product-catalog.yaml) | Run Go tests, then build and scan the image. |
| [Recommendation CI](../.github/workflows/ci-recommendation.yaml) | Test recommendations and telemetry startup, then build and scan the image. |
| [Upstream image scans](../.github/workflows/scan-upstream-images.yaml) | Check the configured public images daily and on manual dispatch. |

Infrastructure CI runs on every PR and push to `main`. It renders the Helm sources
and values configured in Argo CD, checks Kubernetes schemas, and applies custom
rules for images, access, networking, storage and telemetry. Tests also cover
credential initialization, release safeguards, monitoring rules and selected
component behavior using local Docker containers.

These checks run without AWS credentials or a live cluster. They provide coverage
for configuration and component behavior, rather than a complete deployment test.
Some custom rules describe the current architecture and need review when that
architecture intentionally changes.

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

## Public image scans

The scheduled workflow inventories images from the configured charts, including
init containers and selected images created by controllers. It scans public images;
private ECR application images have their build-time scan.

Reports include unfixed HIGH/CRITICAL findings, while only findings with available
fixes block the vulnerability gate. JSON reports are retained as workflow artifacts.
A finding requires investigation and an image/configuration update; the scan does
not upgrade the environment automatically.

## Required checks

Required status checks must be enabled through GitHub repository rules; workflow
files alone do not enforce them. The [setup helper](../.github/scripts/require-infrastructure-checks.py)
can require the two infrastructure jobs on `main` while allowing Image Updater
write-back. The helper requires Image Updater to own the sole writable deploy key.
Running it changes repository settings and requires suitable GitHub permissions.

For local validation, use the tool versions in Infrastructure CI. In a Python
virtual environment, run:

```sh
python3 -m pip install -r .github/scripts/requirements.txt
bash .github/scripts/validate-kubernetes.sh
```

The validator requires Helm, kubeconform and network access for external charts
and schemas. Individual service READMEs describe their test commands.
