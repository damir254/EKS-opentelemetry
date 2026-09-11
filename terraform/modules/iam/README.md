# Controller and publishing identities

Each controller has a dedicated EKS Pod Identity role, restricted in its trust
policy to this cluster ARN, namespace and ServiceAccount. ESO reads the six
project secrets. Image Updater reads the three project ECR repositories; ECR
requires `GetAuthorizationToken` on `*`. GitHub's separate federated role can
push only to those repositories, with exact repository/main-branch OIDC trust.

The Load Balancer Controller policy is vendored from the [official v3.5.0
policy](https://github.com/kubernetes-sigs/aws-load-balancer-controller/blob/v3.5.0/docs/install/iam_policy.json).
Terraform removes WAF/Shield actions (those integrations are disabled in the
platform chart), restricts supported tagged operations to this cluster's exact
tag, and scopes resource ARNs to the current region and account. Other upstream
read and creation actions retain `*` where the controller/API needs discovery or
has no pre-existing resource. This dedicated role does not grant administrator
access. Review upstream policy changes whenever the pinned controller is upgraded.
The upstream JSON is preserved verbatim for review and provenance.

GitHub repositories created from July 15, 2026 use an immutable OIDC subject:
`repo:damir254@OWNER_ID/EKS-opentelemetry@REPO_ID:ref:refs/heads/main`.
Supply the **new** repository's IDs. The explicitly configurable legacy form is
only for repositories verified to still emit it; neither form uses wildcards.
GitHub's OIDC provider is account-wide and can be supplied as an existing ARN.

References: [EKS trust model](https://docs.aws.amazon.com/eks/latest/userguide/pod-id-role.html),
[GitHub OIDC subjects](https://docs.github.com/en/actions/reference/security/oidc),
[GitHub AWS credentials action](https://github.com/aws-actions/configure-aws-credentials).
