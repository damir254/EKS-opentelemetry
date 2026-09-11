output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  value      = [for az in var.availability_zones : aws_subnet.public[az].id]
  depends_on = [aws_route_table_association.public]
}

output "private_subnet_ids" {
  value = [for az in var.availability_zones : aws_subnet.private[az].id]
  # Nodes must not start before their outbound NAT routes are ready.
  depends_on = [aws_route_table_association.private]
}

output "nat_gateway_ids" {
  value = { for az, nat in aws_nat_gateway.this : az => nat.id }
}
