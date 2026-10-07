resource "aws_db_subnet_group" "grafana" {
  name       = "${var.cluster_name}-grafana"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "grafana" {
  name_prefix = "${var.cluster_name}-grafana-db-"
  # AWS makes descriptions immutable; retain it to preserve the live group.
  description = "Private Grafana PostgreSQL, reachable from EKS nodes only"
  vpc_id      = var.vpc_id

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "postgres" {
  security_group_id            = aws_security_group.grafana.id
  referenced_security_group_id = var.cluster_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "Grafana, Keycloak and database bootstrap from EKS"
}

resource "aws_db_parameter_group" "grafana" {
  name_prefix = "${var.cluster_name}-grafana-"
  family      = "postgres16"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# A stable name per lifecycle, regenerated when this module is recreated.
# This avoids final-snapshot collisions without adding another provider.
resource "terraform_data" "final_snapshot" {
  input = "${var.cluster_name}-grafana-final-${formatdate("YYYYMMDDhhmmss", timestamp())}"

  lifecycle {
    ignore_changes = [input]
  }
}

# Preserve the existing resource address and identifier when sharing the instance.
resource "aws_db_instance" "grafana" {
  identifier                      = "${var.cluster_name}-grafana"
  engine                          = "postgres"
  engine_version                  = var.engine_version
  instance_class                  = var.instance_class
  db_name                         = var.snapshot_identifier == null ? "grafana" : null
  username                        = var.snapshot_identifier == null ? "grafana_admin" : null
  snapshot_identifier             = var.snapshot_identifier
  manage_master_user_password     = true
  db_subnet_group_name            = aws_db_subnet_group.grafana.name
  vpc_security_group_ids          = [aws_security_group.grafana.id]
  parameter_group_name            = aws_db_parameter_group.grafana.name
  publicly_accessible             = false
  multi_az                        = var.multi_az
  storage_type                    = "gp2"
  allocated_storage               = 20
  max_allocated_storage           = 0
  storage_encrypted               = true
  ca_cert_identifier              = "rds-ca-rsa2048-g1"
  auto_minor_version_upgrade      = true
  apply_immediately               = false
  backup_retention_period         = var.backup_retention_days
  copy_tags_to_snapshot           = true
  deletion_protection             = var.deletion_protection
  skip_final_snapshot             = false
  final_snapshot_identifier       = terraform_data.final_snapshot.output
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  lifecycle {
    # The restore input selects the initial data source, not a future replacement.
    ignore_changes = [snapshot_identifier, db_name, username]
  }
}
