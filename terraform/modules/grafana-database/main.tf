resource "aws_db_subnet_group" "grafana" {
  name       = "${var.cluster_name}-grafana"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "grafana" {
  name_prefix = "${var.cluster_name}-grafana-db-"
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
  description                  = "Grafana and administration from EKS"
}

resource "aws_db_parameter_group" "grafana" {
  name_prefix = "${var.cluster_name}-grafana-"
  family      = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "grafana" {
  identifier                      = "${var.cluster_name}-grafana"
  engine                          = "postgres"
  engine_version                  = var.engine_version
  instance_class                  = var.instance_class
  db_name                         = "grafana"
  username                        = "grafana_admin"
  manage_master_user_password     = true
  db_subnet_group_name            = aws_db_subnet_group.grafana.name
  vpc_security_group_ids          = [aws_security_group.grafana.id]
  parameter_group_name            = aws_db_parameter_group.grafana.name
  publicly_accessible             = false
  multi_az                        = true
  storage_type                    = "gp3"
  allocated_storage               = 20
  max_allocated_storage           = 100
  storage_encrypted               = true
  ca_cert_identifier              = "rds-ca-rsa2048-g1"
  auto_minor_version_upgrade      = true
  apply_immediately               = false
  backup_retention_period         = 7
  copy_tags_to_snapshot           = true
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${var.cluster_name}-grafana-final"
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
}
