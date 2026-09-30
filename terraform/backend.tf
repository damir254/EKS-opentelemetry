terraform {
  backend "s3" {
    key          = "eks-opentelemetry/terraform.tfstate"
    bucket       = "damir254-eks-opentelemetry-tfstate"
    region       = "eu-central-1"
    encrypt      = true
    use_lockfile = true
  }
}
