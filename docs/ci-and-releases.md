# CI and release gates

Infrastructure CI runs on every PR and push to `main`, including GitOps commits.
It validates both Terraform configurations without AWS credentials, lints
workflows, renders the actual Argo CD Helm sources, validates built-in/custom
resource schemas, and runs database-bootstrap and release-gate tests. It also tests
browser ingestion against pinned Envoy/Collector images on an isolated Docker
network. Missing
schemas, duplicate YAML keys and floating images in project-owned workloads fail
validation. Chart-managed images follow their pinned chart versions.
Grafana validation requires AWS-backed admin credentials for both the server and
reload sidecars; recovery tests check password handling and sync prerequisites.

Service delivery uses **one build**:

`tests → OCI build + SBOM/provenance → Trivy → candidate upload → digest check → Cosign sign/verify → release tag`

Trivy scans the unpacked OCI layout. Skopeo publishes the original archive with
`--all --preserve-digests`, preserving its image index and attestations. Only
after signing and verification does CI create `release-<40-character commit SHA>`.
Image Updater accepts only that pattern; candidate/legacy/signature tags are
ineligible. Existing SHA tags in `dev-images.yaml` remain usable until the next
successful service build updates them. Never manually create release tags.

[Upstream/platform scans](../.github/workflows/scan-upstream-images.yaml) run daily
at **04:43 UTC** and on manual dispatch, without AWS credentials or a cluster.
They render the configured Argo CD charts and inventory public workload/init
images, including Istio injection and Prometheus/Alertmanager/reloader images.
Trivy reports all High/Critical findings; available fixes fail the job. Each
image gets a job summary and a JSON artifact retained for 14 days. Other matrix
jobs continue when one image fails. Private ECR service images retain their
build-time gates; EKS-managed add-on/node components are outside this inventory.
These scans report findings; digest upgrades still require reviewed changes.

PR jobs receive no AWS/OIDC publishing permission. Publication runs only on a
push to `main`. No credentials were added to Git. Failed signing can leave an
ineligible candidate tag; inspect/remove failed candidates during registry cleanup.
Rebuilding an already released commit can fail because its release tag is immutable;
publish a new commit rather than replacing an existing release.

## Require checks before merging

GitHub's `main` branch was unprotected when inspected. Workflow files alone do not
make passing checks mandatory. **After pushing these files**, run:

```bash
python3 .github/scripts/require-infrastructure-checks.py
```

The script requires the enabled workflow to exist on `main`, then creates/updates
a ruleset requiring **Terraform validation** and **Kubernetes and workflow validation**
from the GitHub Actions app. Human updates must have passing checks; use PRs.
GitHub's deploy-key bypass covers all deploy keys, so the script requires Image
Updater to own the **sole writable key**. Its automatic Git write-back continues;
treat that SSH key as privileged and do not add other writable deploy keys.
Use a dedicated GitHub App for a separately scoped bypass if more automation is
needed. [GitHub ruleset API](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset).
Other rulesets remain unchanged. No GitHub rules were changed locally.

## Update pins deliberately

Demo/dependency/init images use registry-verified digests. The readable tag before
`@sha256:` does not control the downloaded contents. Metrics Server is pinned to
`v0.9.0-eksbuild.11`, verified compatible with EKS 1.36 in `eu-central-1` on 2026-10-06.
Refresh image digests through reviewed changes; verify add-on compatibility before
upgrading Kubernetes. Terraform never resolves a floating Metrics Server version.

Frontend Proxy uses official Envoy **1.39.2**, pinned by digest, with patched
OpenSSL `3.0.2-0ubuntu1.30`. Its demo routing/telemetry configuration is rendered
from [this template](../helm/otel-demo/files/frontend-proxy/envoy.yaml.tpl) into a
ConfigMap mounted at `/etc/envoy`. Configuration changes update the pod checksum
and trigger a rollout; the container still runs as UID/GID 101.

Local checks (install the pinned tools from the workflow first):

```bash
python3 -m pip install -r .github/scripts/requirements.txt
bash .github/scripts/validate-kubernetes.sh
python3 -m unittest discover -s .github/scripts/tests -v
python3 -m unittest discover -s platform/monitoring/database/tests -v
python3 .github/scripts/test-browser-telemetry.py # Linux and Docker required
actionlint .github/workflows/*.yaml
terraform fmt -check -recursive terraform
terraform -chdir=terraform validate
terraform -chdir=terraform/bootstrap validate
```
