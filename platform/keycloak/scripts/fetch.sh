#!/bin/sh
set -eu
umask 077

# Only identifiers are passed as command arguments; secret values go to RAM files.
aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
  --query 'DBInstances[0].{host:Endpoint.Address,port:Endpoint.Port,database:DBName,adminUsername:MasterUsername,status:DBInstanceStatus}' \
  --output json > /work/database.json
admin_secret_arn=$(aws rds describe-db-instances \
  --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
  --query 'DBInstances[0].MasterUserSecret.SecretArn' --output text)
aws secretsmanager get-secret-value --secret-id "$admin_secret_arn" \
  --query SecretString --output text > /work/admin.json

# A new Terraform-managed secret container has no versions. Any other read
# failure must stop initialization, rather than silently replacing credentials.
version_count=$(aws secretsmanager describe-secret --secret-id keycloak-credentials \
  --query 'length(VersionIdsToStages || `{}`)' --output text)
if [ "$version_count" -eq 0 ]; then
  printf '{}\n' > /work/existing.json
else
  aws secretsmanager get-secret-value --secret-id keycloak-credentials \
    --query SecretString --output text > /work/existing.json
fi
