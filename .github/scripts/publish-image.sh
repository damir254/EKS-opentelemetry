#!/usr/bin/env bash
# The release tag is the deployment gate. Never expose it before digest and signature checks.
set -euo pipefail
: "${ECR_IMAGE:?}" "${OCI_ARCHIVE:?}" "${EXPECTED_DIGEST:?}" "${SKOPEO_IMAGE:?}"
: "${CERTIFICATE_IDENTITY:?}" "${GITHUB_SHA:?}" "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}"
[[ "$EXPECTED_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$SKOPEO_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]]
[[ -f "$OCI_ARCHIVE" ]]

artifact_dir=$(cd -- "$(dirname -- "$OCI_ARCHIVE")" && pwd)
archive_name=$(basename -- "$OCI_ARCHIVE")
auth_dir=${DOCKER_CONFIG:-$HOME/.docker}
candidate="candidate-${GITHUB_SHA}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
release="release-${GITHUB_SHA}"

copy_image() {
  docker run --rm \
    --volume "$artifact_dir:/work" --volume "$auth_dir:/auth:ro" \
    "$SKOPEO_IMAGE" copy --authfile /auth/config.json \
    --all --preserve-digests "$@"
}

copy_image --digestfile /work/published.digest \
  "oci-archive:/work/$archive_name" "docker://$ECR_IMAGE:$candidate"
[[ "$(cat "$artifact_dir/published.digest")" == "$EXPECTED_DIGEST" ]] || {
  printf 'Published digest differs from the scanned OCI artifact; release blocked.\n' >&2
  exit 1
}

cosign sign --yes "${ECR_IMAGE}@${EXPECTED_DIGEST}"
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity "$CERTIFICATE_IDENTITY" \
  "${ECR_IMAGE}@${EXPECTED_DIGEST}"

# Retag the verified digest; skopeo preserves the complete index/attestations.
copy_image --digestfile /work/release.digest \
  "docker://$ECR_IMAGE@$EXPECTED_DIGEST" "docker://$ECR_IMAGE:$release"
[[ "$(cat "$artifact_dir/release.digest")" == "$EXPECTED_DIGEST" ]]
printf 'Released %s:%s at %s\n' "$ECR_IMAGE" "$release" "$EXPECTED_DIGEST"
