variable "cluster_name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "cluster_security_group_id" {
  type = string
}

variable "instance_class" {
  type = string
}

variable "engine_version" {
  type = string
}

variable "multi_az" {
  description = "Whether to provision a standby database in another AZ."
  type        = bool
}

variable "backup_retention_days" {
  description = "Days of automatic backups; 0 disables them."
  type        = number

  validation {
    condition     = var.backup_retention_days >= 0 && var.backup_retention_days <= 35 && floor(var.backup_retention_days) == var.backup_retention_days
    error_message = "Backup retention must be a whole number between 0 and 35 days."
  }
}

variable "snapshot_identifier" {
  description = "Snapshot to restore when creating the shared RDS instance; null creates an empty database."
  type        = string
  default     = null
}

variable "deletion_protection" {
  description = "Disable only for an intentional, backed-up teardown."
  type        = bool
  default     = true
}
