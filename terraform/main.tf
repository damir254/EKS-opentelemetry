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

# DNS delegation and the ACM certificate survive runtime teardown.
data "aws_route53_zone" "dashboards" {
  name         = "damircloud.com."
  private_zone = false
}

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
  metrics_server_version = var.metrics_server_version
  enable_metrics_server  = var.enable_metrics_server
  builtin_node_pools     = var.auto_mode_builtin_node_pools
}

module "ecr" {
  source = "./modules/ecr"

  repository_names = toset(["product-catalog", "payment", "recommendation"])
  force_delete     = var.ecr_force_delete
}

# Grafana uses this shared backend; Argo CD initializes its application login.
module "grafana_database" {
  source = "./modules/grafana-database"

  cluster_name              = var.cluster_name
  vpc_id                    = module.vpc.vpc_id
  private_subnet_ids        = module.vpc.private_subnet_ids
  cluster_security_group_id = module.eks.cluster_security_group_id
  instance_class            = var.grafana_db_instance_class
  engine_version            = var.grafana_db_engine_version
  multi_az                  = var.grafana_db_multi_az
  backup_retention_days     = var.grafana_db_backup_retention_days
}

module "secrets_manager" {
  source = "./modules/secrets-manager"

  secret_names = toset([
    "postgres-admin-password",
    "astronomy-db-password",
    "monitoring-db-password",
    "grafana-db-credentials",
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
  grafana_database_arn         = module.grafana_database.arn
  grafana_admin_secret_arn     = module.grafana_database.connection.admin_secret_arn
  grafana_credentials_arn      = module.secrets_manager.secret_arns["grafana-db-credentials"]
  external_dns_zone_id         = data.aws_route53_zone.dashboards.zone_id
  dashboard_dns_names          = concat(["argocd.damircloud.com", "grafana.damircloud.com"], local.demo_dns_names)
  github_repository            = "damir254/EKS-opentelemetry"
  github_owner_id              = var.github_owner_id
  github_repository_id         = var.github_repository_id
  github_use_immutable_subject = var.github_use_immutable_subject
  github_oidc_provider_arn     = var.github_oidc_provider_arn
}
