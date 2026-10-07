# Retain this certificate and DNS validation during targeted EKS/RDS/VPC teardown.
locals {
  keycloak_hostname = yamldecode(file("${path.module}/../platform/dashboard-access/values.yaml")).keycloakHostname
}

resource "aws_acm_certificate" "identity" {
  domain_name       = local.keycloak_hostname
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "identity_certificate_validation" {
  zone_id = data.aws_route53_zone.dashboards.zone_id
  name    = one(aws_acm_certificate.identity.domain_validation_options).resource_record_name
  type    = one(aws_acm_certificate.identity.domain_validation_options).resource_record_type
  records = [one(aws_acm_certificate.identity.domain_validation_options).resource_record_value]
  ttl     = 300
}

resource "aws_acm_certificate_validation" "identity" {
  certificate_arn         = aws_acm_certificate.identity.arn
  validation_record_fqdns = [aws_route53_record.identity_certificate_validation.fqdn]

  timeouts {
    create = "15m"
  }
}

output "keycloak_certificate_arn" {
  description = "Issued certificate to attach to the existing shared ALB's certificateARNs."
  value       = aws_acm_certificate_validation.identity.certificate_arn
}
