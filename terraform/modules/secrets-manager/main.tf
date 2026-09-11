# Containers only: values must be supplied through Secrets Manager after apply.
# The AWS-managed aws/secretsmanager KMS key encrypts these secrets by default.
# No secret_version, random_password or secret value data source belongs here.
resource "aws_secretsmanager_secret" "this" {
  for_each = var.secret_names

  name                    = each.key
  description             = "EKS OpenTelemetry demo: ${each.key}"
  recovery_window_in_days = var.recovery_window_in_days
}
