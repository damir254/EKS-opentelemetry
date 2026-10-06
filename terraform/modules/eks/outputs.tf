output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  value = aws_eks_cluster.this.endpoint
}

output "cluster_security_group_id" {
  value = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}

output "auto_mode_node_pools" {
  value = aws_eks_cluster.this.compute_config[0].node_pools
}

output "node_role_arn" {
  value = aws_iam_role.nodes.arn
}

output "addon_versions" {
  value = { metrics-server = aws_eks_addon.metrics_server.addon_version }
}
