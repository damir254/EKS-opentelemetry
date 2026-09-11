# Shared, non-secret environment defaults. Put account-specific settings in the
# ignored local.auto.tfvars using local.auto.tfvars.example as the starting point.
region             = "eu-central-1"
cluster_name       = "otel-demo-eks"
availability_zones = ["eu-central-1a", "eu-central-1b"]
kubernetes_version = "1.36"

single_nat_gateway  = false
node_instance_types = ["c7i-flex.large"]
node_desired_size   = 2
node_min_size       = 2
node_max_size       = 4
node_disk_size      = 50
