"""Retain Keycloak credentials and safely prepare an isolated PostgreSQL login."""

import base64
import json
import os
from pathlib import Path
import re
import secrets
import sys

SECRET_FIELDS = ("password", "bootstrap_client_secret", "configuration_client_secret",
                 "grafana_client_secret", "admin_password", "operator_password")


def write_private(path, value):
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def prepare(directory):
    database, admin, existing = [json.loads((directory / name).read_text())
                                for name in ("database.json", "admin.json", "existing.json")]
    if not all(isinstance(item, dict) for item in (database, admin, existing)):
        raise ValueError("Metadata and credentials must be objects")
    if database["status"] != "available" or database["database"] not in ("grafana", None):
        raise ValueError("Unexpected shared database")
    host, port = database["host"], database["port"]
    if not isinstance(host, str) or not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
        raise ValueError("Invalid endpoint")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    if (admin["username"] != database["adminUsername"]
            or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", admin["username"])):
        raise ValueError("Unexpected administrator")
    if existing and (existing.get("username") != "keycloak"
                     or existing.get("admin_username") != "platform-admin"
                     or existing.get("operator_username") != "operator"):
        raise ValueError("Unexpected existing application credentials")
    fields = {key: existing[key] if existing else secrets.token_urlsafe(48)
              for key in SECRET_FIELDS}
    for value in [admin["password"], *fields.values()]:
        if (not isinstance(value, str) or not value or value != value.strip()
                or any(character in value for character in "\x00\r\n")):
            raise ValueError("Invalid credential")
    credentials = {
        "username": "keycloak", "admin_username": "platform-admin", "operator_username": "operator",
        "db_url": f"jdbc:postgresql://{host}:{port}/keycloak?sslmode=verify-full&sslrootcert=/etc/keycloak/rds-ca/bundle.pem",
        **fields,
    }
    write_private(directory / "application.json", json.dumps(credentials))
    write_private(directory / "publish-needed", "yes" if credentials != existing else "no")
    for name, value in [("host", host), ("port", str(port)), ("admin-username", admin["username"])]:
        write_private(directory / name, value)
    escaped = [str(value).replace("\\", "\\\\").replace(":", "\\:")
               for value in (host, port, "*", admin["username"], admin["password"])]
    write_private(directory / "admin.pgpass", ":".join(escaped) + "\n")
    encoded = base64.b64encode(fields["password"].encode()).decode()
    # CREATE DATABASE cannot run in a transaction. psql gexec quotes values safely.
    sql = f"""SELECT 'CREATE ROLE keycloak LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'keycloak')
\\gexec
DO $check_role$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'keycloak'
               AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
        RAISE EXCEPTION 'Keycloak login has unexpected administrative privileges';
    END IF;
END;
$check_role$;
SELECT format('ALTER ROLE keycloak LOGIN PASSWORD %L',
              convert_from(decode('{encoded}', 'base64'), 'UTF8'))
\\gexec
GRANT keycloak TO CURRENT_USER;
SELECT 'CREATE DATABASE keycloak OWNER keycloak'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'keycloak')
\\gexec
ALTER DATABASE keycloak OWNER TO keycloak;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
\\connect keycloak
ALTER SCHEMA public OWNER TO keycloak;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
"""
    write_private(directory / "initialize.sql", sql)


if __name__ == "__main__":
    os.umask(0o077)
    try:
        prepare(Path(sys.argv[1]))
    except (ValueError, KeyError, TypeError, OSError):
        raise SystemExit("Keycloak credential preparation failed; check secret structure and RDS readiness") from None
