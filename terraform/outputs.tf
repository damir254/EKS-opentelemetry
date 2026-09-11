output "account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "region" {
  value = var.region
}

output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "availability_zones" {
  value = var.availability_zones
}

output "public_subnet_ids" {
  value = module.vpc.public_subnet_ids
}

output "private_subnet_ids" {
  value = module.vpc.private_subnet_ids
}

output "nat_gateway_ids" {
  value = module.vpc.nat_gateway_ids
}

output "node_group" {
  value = module.eks.node_group
}

output "addon_versions" {
  value = module.eks.addon_versions
}

output "ecr_registry" {
  value = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.${data.aws_partition.current.dns_suffix}"
}

output "ecr_repository_urls" {
  value = module.ecr.repository_urls
}

output "secret_names" {
  value = module.secrets_manager.secret_names
}

output "secret_arns" {
  value = module.secrets_manager.secret_arns
}

output "github_actions_role_arn" {
  value = module.iam.github_actions_role_arn
}

output "github_oidc_subject" {
  value = module.iam.github_oidc_subject
}

output "pod_identity_role_arns" {
  value = merge(module.iam.pod_identity_role_arns, { vpc_cni = module.eks.vpc_cni_role_arn })
}

output "update_kubeconfig_command" {
  description = "Run while authenticated as cluster_admin_role_arn. Add --role-arn only when your current identity can assume a different configured administration role."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}
