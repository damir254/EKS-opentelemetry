output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}

output "github_oidc_subject" {
  value = local.github_oidc_subject
}

output "pod_identity_role_arns" {
  value = { for name, role in aws_iam_role.pod_identity : name => role.arn }
}
