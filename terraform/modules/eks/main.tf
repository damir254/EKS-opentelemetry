data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  cluster_arn = "arn:${data.aws_partition.current.partition}:eks:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:cluster/${var.cluster_name}"
  addon_names = toset(["eks-pod-identity-agent", "vpc-cni", "kube-proxy", "coredns", "metrics-server"])
  addon_versions = {
    for name in local.addon_names : name => lookup(var.addon_versions, name, data.aws_eks_addon_version.this[name].version)
  }
}

resource "aws_iam_role" "cluster" {
  name = "${var.cluster_name}-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_cloudwatch_log_group" "cluster" {
  name              = "/aws/eks/${var.cluster_name}/cluster"
  retention_in_days = 7
}

resource "aws_eks_cluster" "this" {
  name                          = var.cluster_name
  role_arn                      = aws_iam_role.cluster.arn
  version                       = var.kubernetes_version
  bootstrap_self_managed_addons = false
  enabled_cluster_log_types     = ["api", "audit", "authenticator"]

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }

  vpc_config {
    subnet_ids              = var.private_subnet_ids
    endpoint_private_access = true
    endpoint_public_access  = true
    # Allow a changing administrator IP; EKS IAM authentication is still required.
    public_access_cidrs = ["0.0.0.0/0"]
  }

  kubernetes_network_config {
    ip_family         = "ipv4"
    service_ipv4_cidr = "172.20.0.0/16"
  }

  # Explicit standard support avoids accidentally enrolling in extended support.
  upgrade_policy {
    support_type = "STANDARD"
  }

  depends_on = [aws_iam_role_policy_attachment.cluster, aws_cloudwatch_log_group.cluster]
}

resource "aws_eks_access_entry" "admin" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.cluster_admin_role_arn
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "admin" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = aws_eks_access_entry.admin.principal_arn
  policy_arn    = "arn:${data.aws_partition.current.partition}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }
}

resource "aws_iam_role" "nodes" {
  name = "${var.cluster_name}-nodes"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "nodes" {
  for_each = toset(["AmazonEKSWorkerNodePolicy", "AmazonEC2ContainerRegistryPullOnly"])

  role       = aws_iam_role.nodes.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/${each.key}"
}

# The CNI gets EC2 networking permissions via its own Pod Identity role instead
# of giving every node (and pods falling back to node credentials) CNI access.
resource "aws_iam_role" "vpc_cni" {
  name = "${var.cluster_name}-vpc-cni"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Condition = {
        StringEquals = {
          "aws:RequestTag/eks-cluster-arn"            = local.cluster_arn
          "aws:RequestTag/kubernetes-namespace"       = "kube-system"
          "aws:RequestTag/kubernetes-service-account" = "aws-node"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "vpc_cni" {
  role       = aws_iam_role.vpc_cni.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKS_CNI_Policy"
}

data "aws_eks_addon_version" "this" {
  for_each = local.addon_names

  addon_name         = each.key
  kubernetes_version = var.kubernetes_version
  most_recent        = true
}

# These DaemonSet add-ons are installed before compute. They use host networking:
# the Pod Identity Agent can provide credentials before the CNI makes nodes Ready.
# Deployment add-ons are deliberately installed after nodes, because waiting for
# CoreDNS/metrics-server health on an empty cluster would block node creation.
resource "aws_eks_addon" "pod_identity_agent" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "eks-pod-identity-agent"
  addon_version               = local.addon_versions["eks-pod-identity-agent"]
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
}

# No self-managed add-ons are bootstrapped. Install kube-proxy before nodes so
# Service routing is available while the CNI and the first pods initialize.
resource "aws_eks_addon" "kube_proxy" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "kube-proxy"
  addon_version               = local.addon_versions["kube-proxy"]
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
}

resource "aws_eks_addon" "vpc_cni" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "vpc-cni"
  addon_version               = local.addon_versions["vpc-cni"]
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  # AWS's documented VPC CNI managed-add-on JSON schema uses a string boolean.
  # https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html
  configuration_values = jsonencode({
    enableNetworkPolicy = "true"
    # Prefix mode avoids the small instance's secondary-IP pod density limit.
    # Enable before creating the managed node group, which calculates maxPods
    # because this launch template does not override the EKS-managed AMI.
    # https://docs.aws.amazon.com/eks/latest/userguide/cni-increase-ip-addresses-procedure.html
    env = {
      ENABLE_PREFIX_DELEGATION = "true"
      WARM_PREFIX_TARGET       = "1"
    }
  })

  pod_identity_association {
    role_arn        = aws_iam_role.vpc_cni.arn
    service_account = "aws-node"
  }

  depends_on = [aws_eks_addon.pod_identity_agent, aws_iam_role_policy_attachment.vpc_cni]
}

resource "aws_launch_template" "nodes" {
  name_prefix            = "${var.cluster_name}-nodes-"
  update_default_version = true

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.node_disk_size
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.cluster_name}-node" }
  }

  tag_specifications {
    resource_type = "volume"
    tags          = { Name = "${var.cluster_name}-node" }
  }
}

resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.cluster_name}-general"
  node_role_arn   = aws_iam_role.nodes.arn
  subnet_ids      = var.private_subnet_ids
  version         = var.kubernetes_version
  ami_type        = "AL2023_x86_64_STANDARD"
  capacity_type   = "ON_DEMAND"
  instance_types  = var.node_instance_types

  scaling_config {
    desired_size = var.node_desired_size
    min_size     = var.node_min_size
    max_size     = var.node_max_size
  }

  update_config {
    max_unavailable = 1
  }

  launch_template {
    id      = aws_launch_template.nodes.id
    version = tostring(aws_launch_template.nodes.latest_version)
  }

  labels = { workload = "general" }

  depends_on = [aws_iam_role_policy_attachment.nodes, aws_eks_addon.vpc_cni, aws_eks_addon.kube_proxy]
}

resource "aws_eks_addon" "after_nodes" {
  for_each = toset(["coredns", "metrics-server"])

  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = each.key
  addon_version               = local.addon_versions[each.key]
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  depends_on = [aws_eks_node_group.this]
}
