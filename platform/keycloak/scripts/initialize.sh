#!/bin/sh
set -eu
umask 077

export PGPASSFILE=/work/admin.pgpass
if ! psql -X --quiet --no-password --set ON_ERROR_STOP=1 \
  --host "$(cat /work/host)" --port "$(cat /work/port)" \
  --username "$(cat /work/admin-username)" --dbname postgres \
  --file /work/initialize.sql > /work/psql.log 2>&1; then
  # psql errors may contain SQL with credentials. Keep logs out of pod output.
  printf '%s\n' 'Keycloak database initialization failed; check RDS connectivity, TLS and administrator permissions.' >&2
  exit 1
fi
rm -f /work/admin.json /work/admin.pgpass /work/initialize.sql /work/psql.log
