variable "cluster_name" {
  type = string
}

variable "kubernetes_version" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "cluster_admin_role_arn" {
  type = string
}

variable "builtin_node_pools" {
  description = "Built-in pools retained temporarily during migration. Empty uses only the custom pool."
  type        = list(string)
  default     = []

  validation {
    condition     = length(distinct(var.builtin_node_pools)) == length(var.builtin_node_pools) && alltrue([for pool in var.builtin_node_pools : contains(["system", "general-purpose"], pool)])
    error_message = "Use only system and/or general-purpose, or an empty list."
  }
}

variable "metrics_server_version" {
  description = "Exact Metrics Server add-on version; never resolve a floating latest version."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+-eksbuild\\.[0-9]+$", var.metrics_server_version))
    error_message = "Supply an exact EKS add-on version such as v0.9.0-eksbuild.11."
  }
}

variable "enable_metrics_server" {
  description = "Install the add-on after custom capacity is configured on a newly created cluster."
  type        = bool
  default     = true
}
