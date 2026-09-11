provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "EKS-opentelemetry"
      Environment = "portfolio"
      ManagedBy   = "Terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

module "vpc" {
  source = "./modules/vpc"

  cluster_name       = var.cluster_name
  cidr_block         = "10.0.0.0/16"
  availability_zones = var.availability_zones
  single_nat_gateway = var.single_nat_gateway
}

module "eks" {
  source = "./modules/eks"

  cluster_name           = var.cluster_name
  kubernetes_version     = var.kubernetes_version
  private_subnet_ids     = module.vpc.private_subnet_ids
  cluster_admin_role_arn = var.cluster_admin_role_arn
  node_instance_types    = var.node_instance_types
  node_desired_size      = var.node_desired_size
  node_min_size          = var.node_min_size
  node_max_size          = var.node_max_size
  node_disk_size         = var.node_disk_size
  addon_versions         = var.addon_versions
}

module "ecr" {
  source = "./modules/ecr"

  repository_names = toset(["product-catalog", "payment", "recommendation"])
  force_delete     = var.ecr_force_delete
}

module "secrets_manager" {
  source = "./modules/secrets-manager"

  secret_names = toset([
    "postgres-admin-password",
    "astronomy-db-password",
    "monitoring-db-password",
    "product-catalog-db-connection-string",
    "accounting-db-connection-string",
    "argocd-image-updater-git-ssh-key",
  ])
  recovery_window_in_days = var.secret_recovery_window_in_days
}

module "iam" {
  source = "./modules/iam"

  cluster_name                 = module.eks.cluster_name
  secret_arns                  = values(module.secrets_manager.secret_arns)
  ecr_repository_arns          = values(module.ecr.repository_arns)
  github_repository            = "damir254/EKS-opentelemetry"
  github_owner_id              = var.github_owner_id
  github_repository_id         = var.github_repository_id
  github_use_immutable_subject = var.github_use_immutable_subject
  github_oidc_provider_arn     = var.github_oidc_provider_arn
}
