output "state_bucket_name" {
  value = aws_s3_bucket.state.id
}

output "backend_config" {
  description = "Write this output to ../backend.hcl for the main root module."
  value       = "bucket = \"${aws_s3_bucket.state.id}\"\n"
}
