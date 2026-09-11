variable "cluster_name" {
  type = string
}

variable "cidr_block" {
  type = string
}

variable "availability_zones" {
  type = list(string)
}

variable "single_nat_gateway" {
  type = bool
}
