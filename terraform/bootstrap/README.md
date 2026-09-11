# Terraform backend bootstrap

This independent configuration creates only the S3 backend bucket with
versioning, SSE-S3 encryption, blocked public access, disabled ACLs and a
TLS-only policy. It uses local state to avoid a dependency on its own bucket.
Keep `terraform.tfstate` and its backup private and backed up outside Git.

Authenticate using AWS IAM Identity Center or an assumed IAM role, then run:

```bash
terraform -chdir=terraform/bootstrap init
terraform -chdir=terraform/bootstrap plan -var="state_bucket_name=eks-opentelemetry-tfstate-YOUR_ACCOUNT_ID" -out=bootstrap.tfplan
terraform -chdir=terraform/bootstrap apply bootstrap.tfplan
terraform -chdir=terraform/bootstrap output -raw backend_config > terraform/backend.hcl
terraform -chdir=terraform init -backend-config=backend.hcl
```

The main backend enables native S3 lock files and requires Terraform >= 1.10;
there is no DynamoDB table. Its operator needs `s3:ListBucket` for the bucket,
`s3:GetObject`/`s3:PutObject` for `eks-opentelemetry/terraform.tfstate`, and
`s3:GetObject`/`s3:PutObject`/`s3:DeleteObject` for its `.tflock` object.
[HashiCorp S3 backend documentation](https://developer.hashicorp.com/terraform/language/backend/s3)
describes exact resource scopes.

`prevent_destroy` deliberately preserves the backend independently of the demo.
After destroying the main environment, retain state backups or explicitly remove
that protection and empty **all object versions/delete markers** if retiring the
backend. Do not reuse state from the original cloud deployment for this new stack.
