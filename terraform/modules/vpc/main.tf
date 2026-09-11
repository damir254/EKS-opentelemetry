locals {
  subnets   = { for index, az in var.availability_zones : az => index }
  nat_zones = var.single_nat_gateway ? toset([var.availability_zones[0]]) : toset(var.availability_zones)
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = var.cluster_name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = var.cluster_name }
}

resource "aws_subnet" "public" {
  for_each = local.subnets

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.cidr_block, 8, each.value)
  map_public_ip_on_launch = false

  tags = {
    Name                                        = "${var.cluster_name}-public-${each.key}"
    "kubernetes.io/role/elb"                    = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  }
}

resource "aws_subnet" "private" {
  for_each = local.subnets

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.cidr_block, 8, each.value + 10)
  map_public_ip_on_launch = false

  tags = {
    Name                                        = "${var.cluster_name}-private-${each.key}"
    "kubernetes.io/role/internal-elb"           = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  }
}

resource "aws_eip" "nat" {
  for_each = local.nat_zones

  domain = "vpc"
  tags   = { Name = "${var.cluster_name}-nat-${each.key}" }
}

resource "aws_nat_gateway" "this" {
  for_each = local.nat_zones

  allocation_id = aws_eip.nat[each.key].id
  subnet_id     = aws_subnet.public[each.key].id
  tags          = { Name = "${var.cluster_name}-nat-${each.key}" }

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${var.cluster_name}-public" }
}

resource "aws_route_table_association" "public" {
  for_each = local.subnets

  subnet_id      = aws_subnet.public[each.key].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  for_each = local.subnets

  vpc_id = aws_vpc.this.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[var.single_nat_gateway ? var.availability_zones[0] : each.key].id
  }

  tags = { Name = "${var.cluster_name}-private-${each.key}" }
}

resource "aws_route_table_association" "private" {
  for_each = local.subnets

  subnet_id      = aws_subnet.private[each.key].id
  route_table_id = aws_route_table.private[each.key].id
}
