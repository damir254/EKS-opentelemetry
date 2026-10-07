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
    external_dns = {
      role_suffix     = "external-dns"
      namespace       = "external-dns"
      service_account = "external-dns"
    }
    grafana_db_bootstrap = {
      role_suffix     = "grafana-db-bootstrap"
      namespace       = "monitoring"
      service_account = "grafana-db-bootstrap"
    }
    keycloak_db_bootstrap = {
      role_suffix     = "keycloak-db-bootstrap"
      namespace       = "identity"
      service_account = "keycloak-db-bootstrap"
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

resource "aws_iam_role_policy" "grafana_db_bootstrap" {
  name = "grafana-database-initialization"
  role = aws_iam_role.pod_identity["grafana_db_bootstrap"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["rds:DescribeDBInstances"]
        Resource = var.grafana_database_arn
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.grafana_admin_secret_arn
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
        ]
        Resource = var.grafana_credentials_arn
      },
    ]
  })
}

resource "aws_iam_role_policy" "keycloak_db_bootstrap" {
  name = "keycloak-database-initialization"
  role = aws_iam_role.pod_identity["keycloak_db_bootstrap"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["rds:DescribeDBInstances"]
        Resource = var.grafana_database_arn
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.grafana_admin_secret_arn
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"]
        Resource = var.keycloak_credentials_arn
      },
    ]
  })
}

resource "aws_iam_role_policy" "external_dns" {
  name = "dashboard-dns-updates"
  role = aws_iam_role.pod_identity["external_dns"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["route53:ListHostedZones", "route53:ListHostedZonesByName"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["route53:ListResourceRecordSets", "route53:ListTagsForResources"]
        Resource = "arn:${data.aws_partition.current.partition}:route53:::hostedzone/${var.external_dns_zone_id}"
      },
      {
        Effect   = "Allow"
        Action   = ["route53:ChangeResourceRecordSets"]
        Resource = "arn:${data.aws_partition.current.partition}:route53:::hostedzone/${var.external_dns_zone_id}"
        Condition = {
          "ForAllValues:StringEquals" = {
            "route53:ChangeResourceRecordSetsNormalizedRecordNames" = concat(
              var.dashboard_dns_names,
              # The pinned ExternalDNS release prefixes A ownership records with "a-".
              [for name in var.dashboard_dns_names : "external-dns.a-${name}"],
            )
            "route53:ChangeResourceRecordSetsRecordTypes" = ["A", "TXT"]
            "route53:ChangeResourceRecordSetsActions"     = ["CREATE", "UPSERT"]
          }
        }
      },
    ]
  })
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
