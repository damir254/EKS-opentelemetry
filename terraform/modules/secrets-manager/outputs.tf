output "secret_names" {
  value = { for name, secret in aws_secretsmanager_secret.this : name => secret.name }
}

output "secret_arns" {
  value = { for name, secret in aws_secretsmanager_secret.this : name => secret.arn }
}
