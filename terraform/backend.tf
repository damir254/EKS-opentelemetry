terraform {
  # Provision this bucket separately with ./bootstrap, then supply its name via
  # -backend-config=backend.hcl. No credentials belong in backend configuration.
  backend "s3" {
    key          = "eks-opentelemetry/terraform.tfstate"
    bucket       = "damir254-eks-opentelemetry-tfstate"
    region       = "eu-central-1"
    encrypt      = true
    use_lockfile = true
  }
}
