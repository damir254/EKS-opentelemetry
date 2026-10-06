output "arn" {
  description = "RDS instance ARN permitted to the database bootstrap Job."
  value       = aws_db_instance.grafana.arn
}

output "connection" {
  description = "Non-secret connection details; the managed administrator password stays in Secrets Manager."
  value = {
    host                  = aws_db_instance.grafana.address
    port                  = aws_db_instance.grafana.port
    database              = aws_db_instance.grafana.db_name
    admin_username        = aws_db_instance.grafana.username
    admin_secret_arn      = aws_db_instance.grafana.master_user_secret[0].secret_arn
    application_username  = "grafana"
    application_secret_id = "grafana-db-credentials"
  }
}
