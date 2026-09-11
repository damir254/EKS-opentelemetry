resource "aws_ecr_repository" "this" {
  for_each = var.repository_names

  name                 = each.key
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.force_delete

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "untagged" {
  for_each = aws_ecr_repository.this

  repository = each.value.name
  # ECR does not know which SHA is deployed. Never expire tagged release images
  # automatically; prune them manually after confirming rollout/rollback needs.
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after seven days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 7
      }
      action = { type = "expire" }
    }]
  })
}
