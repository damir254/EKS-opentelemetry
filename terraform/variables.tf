variable "region" {
  description = "AWS region used by Terraform, GitOps and GitHub Actions."
  type        = string
  default     = "eu-central-1"

  validation {
    condition     = var.region == "eu-central-1"
    error_message = "This portfolio configuration targets eu-central-1."
  }
}

variable "cluster_name" {
  description = "EKS cluster and project resource name prefix."
  type        = string
  default     = "otel-demo-eks"

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{0,34}$", var.cluster_name))
    error_message = "Use a cluster name of up to 35 letters, digits or hyphens beginning with a letter."
  }
}

variable "availability_zones" {
  description = "Two eu-central-1 availability zones, each with public and private subnets."
  type        = list(string)
  default     = ["eu-central-1a", "eu-central-1b"]

  validation {
    condition     = length(var.availability_zones) == 2 && length(distinct(var.availability_zones)) == 2 && alltrue([for az in var.availability_zones : can(regex("^eu-central-1[a-z]$", az))])
    error_message = "Supply exactly two distinct eu-central-1 availability zones."
  }
}

variable "single_nat_gateway" {
  description = "Use one NAT for temporary lower-cost testing. False keeps one NAT per AZ; true loses outbound AZ redundancy and can incur cross-AZ traffic charges."
  type        = bool
  default     = false
}

variable "kubernetes_version" {
  description = "Supported EKS minor version. AWS lists 1.36 in standard support through August 2, 2027 (verified September 10, 2026)."
  type        = string
  default     = "1.36"
}

variable "cluster_admin_role_arn" {
  description = "Existing IAM role ARN granted EKS cluster-admin through an access entry (use an IAM Identity Center or other federated administration role, never an STS session ARN)."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/.+$", var.cluster_admin_role_arn))
    error_message = "Provide an existing IAM role ARN, not a user, root or STS assumed-role session ARN."
  }
}

variable "node_instance_types" {
  description = "Nitro x86_64 managed node instance types compatible with AL2023_x86_64_STANDARD and CNI prefix delegation. Two t3.large nodes provide 4 vCPU / 16 GiB total; monitor burst credits and scheduling capacity during load tests."
  type        = list(string)
  default     = ["t3.medium"]
}

variable "node_desired_size" {
  description = "Desired managed node count. HPA scales pods; no node autoscaler is installed."
  type        = number
  default     = 2
}

variable "node_min_size" {
  description = "Minimum managed node count (two preserves the intended multi-AZ design)."
  type        = number
  default     = 2

  validation {
    condition     = var.node_min_size >= 2 && floor(var.node_min_size) == var.node_min_size
    error_message = "The node group minimum must be an integer of at least two."
  }
}

variable "node_max_size" {
  description = "Managed node group upper bound. It does not install or enable Cluster Autoscaler."
  type        = number
  default     = 4

  validation {
    condition     = var.node_max_size >= var.node_desired_size && var.node_desired_size >= var.node_min_size && floor(var.node_max_size) == var.node_max_size && floor(var.node_desired_size) == var.node_desired_size
    error_message = "Use integer node capacities with min <= desired <= max."
  }
}

variable "node_disk_size" {
  description = "Encrypted gp3 root volume size in GiB for each managed node."
  type        = number
  default     = 50
}

variable "addon_versions" {
  description = "Optional exact EKS add-on versions. Missing entries resolve the latest version compatible with kubernetes_version during plan; inspect addon_versions output and pin entries for a repeatable environment."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for name in keys(var.addon_versions) : contains(["vpc-cni", "coredns", "kube-proxy", "eks-pod-identity-agent", "metrics-server", "aws-ebs-csi-driver"], name)])
    error_message = "Only the six managed project add-on names are accepted."
  }
}

variable "ecr_force_delete" {
  description = "Explicit disposable-environment option to delete repositories containing images during destroy. False protects published images by requiring manual cleanup first."
  type        = bool
  default     = false
}

variable "secret_recovery_window_in_days" {
  description = "Secrets Manager deletion recovery window. Set 0 explicitly for disposable testing if immediate deletion/recreation is required. Secret values are always populated outside Terraform."
  type        = number
  default     = 7

  validation {
    condition     = var.secret_recovery_window_in_days == 0 || (var.secret_recovery_window_in_days >= 7 && var.secret_recovery_window_in_days <= 30 && floor(var.secret_recovery_window_in_days) == var.secret_recovery_window_in_days)
    error_message = "Use 0 for immediate deletion or an integer recovery window from 7 through 30 days."
  }
}

variable "github_owner_id" {
  description = "Numeric owner ID for damir254 from GitHub API, used in the current immutable OIDC subject format."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "Supply the actual numeric GitHub owner ID."
  }
}

variable "github_repository_id" {
  description = "Numeric ID of the NEW damir254/EKS-opentelemetry repository from GitHub API."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "Supply the actual numeric GitHub EKS repository ID."
  }
}

variable "github_use_immutable_subject" {
  description = "New repositories created from July 15, 2026 use owner/repository IDs in OIDC subjects. Set false only after verifying this repository still emits the legacy name-only subject."
  type        = bool
  default     = true
}

variable "github_oidc_provider_arn" {
  description = "Existing account-wide GitHub OIDC provider ARN, if one already exists. Null creates it; do not create duplicate providers for the same issuer."
  type        = string
  default     = null
}
