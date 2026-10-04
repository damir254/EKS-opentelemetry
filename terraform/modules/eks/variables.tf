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
  type    = string
  default = null
}
