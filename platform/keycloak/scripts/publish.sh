#!/bin/sh
set -eu
umask 077

if [ "$(cat /work/publish-needed)" = yes ]; then
  aws secretsmanager put-secret-value --secret-id keycloak-credentials \
    --secret-string file:///work/application.json --query VersionId --output text > /dev/null
fi
rm -f /work/*
printf '%s\n' 'Keycloak RDS application login is ready.'
