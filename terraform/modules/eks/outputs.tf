output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  value = aws_eks_cluster.this.endpoint
}

output "node_group" {
  value = {
    name               = aws_eks_node_group.this.node_group_name
    arn                = aws_eks_node_group.this.arn
    subnet_ids         = aws_eks_node_group.this.subnet_ids
    instance_types     = aws_eks_node_group.this.instance_types
    desired_size       = var.node_desired_size
    min_size           = var.node_min_size
    max_size           = var.node_max_size
    autoscaling_groups = aws_eks_node_group.this.resources[0].autoscaling_groups
    node_role_arn      = aws_iam_role.nodes.arn
  }
}

output "addon_versions" {
  value = local.addon_versions
}

output "vpc_cni_role_arn" {
  value = aws_iam_role.vpc_cni.arn
}
