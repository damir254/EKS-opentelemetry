variable "cluster_name" {
  type = string
}

variable "secret_arns" {
  type = list(string)
}

variable "ecr_repository_arns" {
  type = list(string)
}

variable "grafana_database_arn" {
  type        = string
  description = "RDS instance whose endpoint and administrator secret the bootstrap Job resolves."
}

variable "grafana_admin_secret_arn" {
  type        = string
  description = "RDS-managed administrator secret, readable only by the bootstrap role."
}

variable "grafana_credentials_arn" {
  type        = string
  description = "Application secret that the bootstrap Job initializes and Grafana consumes through ESO."
}

variable "keycloak_credentials_arn" {
  type        = string
  description = "Keycloak database, bootstrap and OIDC credentials maintained by its scoped bootstrap Job."
}

variable "external_dns_zone_id" {
  description = "Existing public hosted zone permitted for platform DNS updates."
  type        = string
}

variable "dashboard_dns_names" {
  description = "Platform hostnames whose aliases and ownership TXT records ExternalDNS may update."
  type        = list(string)
}

variable "github_repository" {
  type = string

  validation {
    condition     = var.github_repository == "damir254/EKS-opentelemetry"
    error_message = "This publishing role is restricted to damir254/EKS-opentelemetry."
  }
}

variable "github_owner_id" {
  type = string
}

variable "github_repository_id" {
  type = string
}

variable "github_use_immutable_subject" {
  type = bool
}

variable "github_oidc_provider_arn" {
  type = string
}
