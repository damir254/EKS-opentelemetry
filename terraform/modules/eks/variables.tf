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

variable "metrics_server_version" {
  description = "Exact Metrics Server add-on version; never resolve a floating latest version."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+-eksbuild\\.[0-9]+$", var.metrics_server_version))
    error_message = "Supply an exact EKS add-on version such as v0.9.0-eksbuild.11."
  }
}
