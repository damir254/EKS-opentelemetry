# Retained alongside Route 53 during targeted EKS/RDS/VPC teardown.
# Hostnames come from the same environment values that Argo CD renders.
locals {
  demo_ingress = yamldecode(file("${path.module}/../helm/otel-demo/environments/dev.yaml")).ingress
  demo_dns_names = concat(
    [local.demo_ingress.host],
    local.demo_ingress.loadGenerator.enabled ? [local.demo_ingress.loadGenerator.host] : [],
  )
}

resource "aws_acm_certificate" "demo" {
  domain_name               = local.demo_ingress.host
  subject_alternative_names = setsubtract(toset(local.demo_dns_names), [local.demo_ingress.host])
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "demo_certificate_validation" {
  for_each = toset(local.demo_dns_names)

  zone_id = data.aws_route53_zone.dashboards.zone_id
  name = one([
    for option in aws_acm_certificate.demo.domain_validation_options : option.resource_record_name
    if option.domain_name == each.key
  ])
  type = one([
    for option in aws_acm_certificate.demo.domain_validation_options : option.resource_record_type
    if option.domain_name == each.key
  ])
  records = [one([
    for option in aws_acm_certificate.demo.domain_validation_options : option.resource_record_value
    if option.domain_name == each.key
  ])]
  ttl = 300
}

resource "aws_acm_certificate_validation" "demo" {
  certificate_arn         = aws_acm_certificate.demo.arn
  validation_record_fqdns = [for record in aws_route53_record.demo_certificate_validation : record.fqdn]

  timeouts {
    create = "15m"
  }
}

output "demo_certificate_arn" {
  description = "Issued public certificate for the demo and Locust; set this ARN in dev.yaml."
  value       = aws_acm_certificate_validation.demo.certificate_arn
}
