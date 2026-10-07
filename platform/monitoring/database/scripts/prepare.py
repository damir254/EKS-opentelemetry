"""Prepare a Grafana login without logging secrets or storing them in Terraform."""

import base64
import json
import os
from pathlib import Path
import re
import secrets
import sys


def write_private(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def admin_credentials(existing):
    """Keep the dashboard login stable, including upgrades of older bundles."""
    keys = {"grafana_admin_user", "grafana_admin_password"}
    present = keys.intersection(existing)
    if present and present != keys:
        raise ValueError("Incomplete Grafana dashboard credentials")
    credentials = {key: existing[key] for key in keys} if present else {
        "grafana_admin_user": "admin",
        "grafana_admin_password": secrets.token_urlsafe(48),
    }
    password = credentials["grafana_admin_password"]
    if credentials["grafana_admin_user"] != "admin":
        raise ValueError("Unexpected Grafana dashboard administrator")
    if (not isinstance(password, str) or not password or password != password.strip()
            or any(c in password for c in "\x00\n\r")):
        raise ValueError("Invalid Grafana dashboard password")
    return credentials


def prepare(directory):
    database = json.loads((directory / "database.json").read_text())
    admin = json.loads((directory / "admin.json").read_text())
    existing = json.loads((directory / "existing.json").read_text())
    if not all(isinstance(value, dict) for value in (database, admin, existing)):
        raise ValueError("Database metadata and credentials must be JSON objects")
    # Restored PostgreSQL instances can omit DBName from RDS metadata. The
    # initialization connection still explicitly requires the grafana database.
    if database["status"] != "available" or database["database"] not in ("grafana", None):
        raise ValueError("Unexpected database or database not yet available")
    host = database["host"]
    port = database["port"]
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid database endpoint")
    if admin["username"] != database["adminUsername"]:
        raise ValueError("Administrator identity does not match RDS")
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", admin["username"]):
        raise ValueError("Invalid administrator identity")

    if existing:
        if existing.get("username") != "grafana":
            raise ValueError("Existing secret is not a Grafana application login")
        password = existing["password"]
        secret_key = existing["secret_key"]
    else:
        password = secrets.token_urlsafe(48)
        secret_key = secrets.token_hex(32)

    for value in (admin["password"], password, secret_key):
        if not isinstance(value, str) or not value or any(c in value for c in "\x00\n\r"):
            raise ValueError("Invalid secret value")
        if value != value.strip():
            raise ValueError("Secret has unsupported surrounding whitespace")
    if len(secret_key) < 32:
        raise ValueError("Grafana encryption key must have at least 32 characters")

    credentials = {
        "host": f"{host}:{port}", "username": "grafana",
        "password": password, "secret_key": secret_key,
        **admin_credentials(existing),
    }
    write_private(directory / "application.json", json.dumps(credentials))
    write_private(directory / "publish-needed", "yes" if credentials != existing else "no")
    write_private(directory / "host", host)
    write_private(directory / "port", str(port))
    write_private(directory / "admin-username", admin["username"])

    def pgpass_escape(value):
        return value.replace("\\", "\\\\").replace(":", "\\:")

    pgpass = ":".join(pgpass_escape(str(value)) for value in
                      (host, port, "grafana", admin["username"], admin["password"]))
    write_private(directory / "admin.pgpass", pgpass + "\n")
    # Base64 contains no SQL quoting characters. PostgreSQL's format(%L) quotes
    # the decoded password safely; existing credentials are preserved on retries.
    encoded_password = base64.b64encode(password.encode()).decode()
    sql = f"""BEGIN;
SELECT 'CREATE ROLE grafana LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana')
\\gexec
DO $check_role$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana'
               AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
        RAISE EXCEPTION 'Grafana login has unexpected administrative privileges';
    END IF;
END;
$check_role$;
SELECT format('ALTER ROLE grafana LOGIN PASSWORD %L',
              convert_from(decode('{encoded_password}', 'base64'), 'UTF8'))
\\gexec
GRANT grafana TO CURRENT_USER;
ALTER DATABASE grafana OWNER TO grafana;
REVOKE ALL ON DATABASE grafana FROM PUBLIC;
ALTER SCHEMA public OWNER TO grafana;
COMMIT;
"""
    write_private(directory / "initialize.sql", sql)


if __name__ == "__main__":
    os.umask(0o077)
    try:
        prepare(Path(sys.argv[1]))
    except (ValueError, KeyError, TypeError, OSError):
        # Validation exceptions can include supplied values; never log them.
        raise SystemExit("Database credential preparation failed; check secret structure and RDS readiness") from None
