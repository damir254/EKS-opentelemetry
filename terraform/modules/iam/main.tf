data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  cluster_arn = "arn:${data.aws_partition.current.partition}:eks:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:cluster/${var.cluster_name}"
  pod_identities = {
    external_secrets = {
      role_suffix     = "external-secrets"
      namespace       = "external-secrets"
      service_account = "external-secrets"
    }
    image_updater = {
      role_suffix     = "image-updater"
      namespace       = "argocd"
      service_account = "argocd-image-updater-controller"
    }
    load_balancer_controller = {
      role_suffix     = "load-balancer-controller"
      namespace       = "kube-system"
      service_account = "aws-load-balancer-controller"
    }
  }
  github_repository_parts = split("/", var.github_repository)
  github_oidc_subject = var.github_use_immutable_subject ? (
    "repo:${local.github_repository_parts[0]}@${var.github_owner_id}/${local.github_repository_parts[1]}@${var.github_repository_id}:ref:refs/heads/main"
  ) : "repo:${var.github_repository}:ref:refs/heads/main"
  github_oidc_provider_arn = var.github_oidc_provider_arn != null ? var.github_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn
}

resource "aws_iam_role" "pod_identity" {
  for_each = local.pod_identities

  name = "${var.cluster_name}-${each.value.role_suffix}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Condition = {
        StringEquals = {
          "aws:RequestTag/eks-cluster-arn"            = local.cluster_arn
          "aws:RequestTag/kubernetes-namespace"       = each.value.namespace
          "aws:RequestTag/kubernetes-service-account" = each.value.service_account
        }
      }
    }]
  })
}

resource "aws_eks_pod_identity_association" "this" {
  for_each = local.pod_identities

  cluster_name    = var.cluster_name
  namespace       = each.value.namespace
  service_account = each.value.service_account
  role_arn        = aws_iam_role.pod_identity[each.key].arn
}

resource "aws_iam_role_policy" "external_secrets" {
  name = "project-secrets-read"
  role = aws_iam_role.pod_identity["external_secrets"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      Resource = var.secret_arns
    }]
  })
}

resource "aws_iam_role_policy" "image_updater" {
  name = "project-ecr-read"
  role = aws_iam_role.pod_identity["image_updater"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:DescribeImages",
          "ecr:DescribeRepositories",
          "ecr:GetDownloadUrlForLayer",
          "ecr:ListImages",
        ]
        Resource = var.ecr_repository_arns
      },
    ]
  })
}

# Derived from the vendored official v3.5.0 policy (see README.md). Keep its
# resource-tag conditions, narrow tagged operations to this cluster, and scope
# resource ARNs to this account/region. WAF and Shield are disabled in GitOps.
locals {
  load_balancer_policy = jsondecode(file("${path.module}/aws-load-balancer-controller-policy-v3.5.0.json"))
  load_balancer_statements = [for statement in local.load_balancer_policy.Statement : merge(statement, {
    Action   = [for action in statement.Action : action if !startswith(action, "waf") && !startswith(action, "shield:") && action != "elasticloadbalancing:SetWebAcl"]
    Resource = [for resource in flatten([statement.Resource]) : replace(resource, ":*:*:", ":${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:")]
    }, can(statement.Condition) ? {
    Condition = merge(statement.Condition, {
      StringEquals = merge(try(statement.Condition.StringEquals, {}), {
        for key, value in try(statement.Condition.Null, {}) : key => var.cluster_name
        if value == "false" && contains(["aws:RequestTag/elbv2.k8s.aws/cluster", "aws:ResourceTag/elbv2.k8s.aws/cluster"], key)
      })
    })
  } : {})]

}

resource "aws_iam_policy" "load_balancer_controller" {
  name = "${var.cluster_name}-load-balancer-controller"
  policy = jsonencode({
    Version   = local.load_balancer_policy.Version
    Statement = [for statement in local.load_balancer_statements : statement if length(statement.Action) > 0]
  })
}

resource "aws_iam_role_policy_attachment" "load_balancer_controller" {
  role       = aws_iam_role.pod_identity["load_balancer_controller"].name
  policy_arn = aws_iam_policy.load_balancer_controller.arn
}

# GitHub is an account-level OIDC provider; EKS Pod Identity does not need an
# EKS per-cluster IAM OIDC provider. Reuse an existing GitHub provider if present.
resource "aws_iam_openid_connect_provider" "github" {
  count = var.github_oidc_provider_arn == null ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # IAM validates this provider using its trusted CA roots; no stale thumbprint.
}

resource "aws_iam_role" "github_actions" {
  name                 = "${var.cluster_name}-github-actions-ecr"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.github_oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = local.github_oidc_subject
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions" {
  name = "project-ecr-push"
  role = aws_iam_role.github_actions.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = var.ecr_repository_arns
      },
    ]
  })
}
