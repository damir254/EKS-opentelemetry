#!/usr/bin/env python3
"""Exercise real PostgreSQL/Keycloak startup, isolation and safe reconciliation."""

import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "platform/keycloak"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    instance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(instance)
    return instance


def run(*args, input=None):
    result = subprocess.run(args, input=input, text=True, capture_output=True)
    if result.returncode:
        # Container/SQL errors may contain credentials; show only the operation.
        raise RuntimeError("Integration test operation failed: " + " ".join(args[:3]))
    return result.stdout.strip()


def wait_ready(url):
    for attempt in range(120):
        try:
            with urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except (URLError, OSError):
            pass
        time.sleep(2)
    raise RuntimeError("Keycloak startup did not become healthy")


def main():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    prepare = module("keycloak_prepare", CHART / "scripts/prepare.py")
    configuration = module("keycloak_configure", CHART / "scripts/configure.py")
    realm = json.loads((CHART / "realm.json").read_text())
    prefix = "keycloak-ci-" + uuid.uuid4().hex[:10]
    postgres, keycloak = prefix + "-postgres", prefix + "-server"
    network, volume = prefix + "-network", prefix + "-distribution"
    with tempfile.TemporaryDirectory(prefix="keycloak-ci-") as directory:
        work = Path(directory)
        admin_password = secrets.token_urlsafe(32)
        inputs = {"database.json": {"host": postgres, "port": 5432, "database": "grafana",
                                    "adminUsername": "postgres", "status": "available"},
                  "admin.json": {"username": "postgres", "password": admin_password}, "existing.json": {}}
        for name, data in inputs.items():
            (work / name).write_text(json.dumps(data))
            (work / name).chmod(0o600)
        prepare.prepare(work)
        credentials = json.loads((work / "application.json").read_text())
        environment = work / "postgres.env"
        environment.write_text("POSTGRES_DB=grafana\nPOSTGRES_PASSWORD=" + admin_password + "\n")
        environment.chmod(0o600)
        run("docker", "network", "create", network)
        run("docker", "volume", "create", volume)
        try:
            run("docker", "run", "-d", "--name", postgres, "--network", network,
                "--env-file", str(environment), values["images"]["postgres"])
            for attempt in range(60):
                if subprocess.run(["docker", "exec", postgres, "pg_isready", "-U", "postgres"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("PostgreSQL did not become ready")
            run("docker", "exec", "-i", postgres, "psql", "-X", "-U", "postgres", "-v", "ON_ERROR_STOP=1", input=
                "CREATE ROLE grafana; ALTER DATABASE grafana OWNER TO grafana; REVOKE ALL ON DATABASE grafana FROM PUBLIC;")
            sql = (work / "initialize.sql").read_text()
            for attempt in range(2):
                run("docker", "exec", "-i", postgres, "psql", "-X", "-U", "postgres", "-v", "ON_ERROR_STOP=1", input=sql)
            isolation = run("docker", "exec", postgres, "psql", "-X", "-U", "postgres", "-Atc",
                            "SELECT has_database_privilege('keycloak','grafana','CONNECT'), rolcreatedb, rolcreaterole, rolsuper FROM pg_roles WHERE rolname='keycloak';")
            assert isolation == "f|f|f|f", "Keycloak must not access Grafana or obtain administration privileges"
            image = values["images"]["keycloak"]
            run("docker", "run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "/bin/bash",
                "-v", volume + ":/runtime", image, "-ec", "cp -R /opt/keycloak/. /runtime/; chown -R 1000:1000 /runtime")
            run("docker", "run", "--rm", "--network", "none", "--user", "1000:1000", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "2g",
                "--tmpfs", "/tmp:uid=1000,gid=1000", "-v", volume + ":/opt/keycloak", image,
                "build", "--db=postgres", "--health-enabled=true", "--metrics-enabled=true")
            environment = work / "keycloak.env"
            environment.write_text("\n".join([
                "KC_HOSTNAME=https://auth.damircloud.com", "KC_HTTP_ENABLED=true",
                "KC_HTTP_MANAGEMENT_SCHEME=http", "KC_BOOTSTRAP_ADMIN_CLIENT_ID=platform-bootstrap",
                "KC_BOOTSTRAP_ADMIN_CLIENT_SECRET=" + credentials["bootstrap_client_secret"],
                "KC_DB_URL=jdbc:postgresql://" + postgres + ":5432/keycloak",
                "KC_DB_USERNAME=keycloak", "KC_DB_PASSWORD=" + credentials["password"],
                "KC_DB_POOL_MIN_SIZE=2", "KC_DB_POOL_MAX_SIZE=8", "KC_DB_POOL_INITIAL_SIZE=2",
            ]) + "\n")
            environment.chmod(0o600)
            run("docker", "run", "-d", "--name", keycloak, "--network", network, "--user", "1000:1000",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "2g",
                "--tmpfs", "/tmp:uid=1000,gid=1000", "-v", volume + ":/opt/keycloak",
                "-p", "127.0.0.1::8080", "-p", "127.0.0.1::9000", "--env-file", str(environment), image,
                "start", "--optimized")
            ports = json.loads(run("docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", keycloak))
            base = "http://127.0.0.1:" + ports["8080/tcp"][0]["HostPort"]
            health = "http://127.0.0.1:" + ports["9000/tcp"][0]["HostPort"] + "/health/ready"
            wait_ready(health)
            for path in ("/health/started", "/health/live"):
                with urlopen(health.removesuffix("/health/ready") + path) as response:
                    assert response.status == 200
            api = configuration.API(base)
            configuration.configure(api, realm, credentials)
            api.authenticate("platform", "platform-configurator", credentials["configuration_client_secret"])
            flow = api.request("GET", "/admin/realms/platform/authentication/flows/platform-browser/executions")
            assert next(item for item in flow if item.get("providerId") == "auth-otp-form")["requirement"] == "REQUIRED"
            users = api.request("GET", "/admin/realms/platform/users?username=operator&exact=true")
            user_id = users[0]["id"]
            groups = api.request("GET", f"/admin/realms/platform/users/{user_id}/groups")
            assert [group["name"] for group in groups] == ["platform-admins"]
            group_id = groups[0]["id"]
            api.request("PUT", f"/admin/realms/platform/users/{user_id}/reset-password",
                        {"type": "password", "value": secrets.token_urlsafe(32), "temporary": False})
            password_id = next(item["id"] for item in api.request("GET", f"/admin/realms/platform/users/{user_id}/credentials")
                               if item["type"] == "password")
            api.request("DELETE", f"/admin/realms/platform/users/{user_id}/groups/{group_id}")
            configuration.configure(api, realm, credentials)
            unchanged = api.request("GET", f"/admin/realms/platform/users/{user_id}/credentials")
            assert any(item["id"] == password_id for item in unchanged), "A sync must preserve changed passwords"
            assert api.request("GET", f"/admin/realms/platform/users/{user_id}/groups") == [], "A sync must preserve access revocation"
            api.request("DELETE", f"/admin/realms/platform/users/{user_id}")
            configuration.configure(api, realm, credentials)
            assert api.request("GET", "/admin/realms/platform/users?username=operator&exact=true") == [], "A sync must not recreate deleted operators"
            try:
                api.request("GET", "/admin/realms/master")
            except configuration.APIError as error:
                assert error.status == 403
            else:
                raise AssertionError("The configurator must be restricted to the platform realm")
            try:
                api.authenticate("master", "platform-bootstrap", credentials["bootstrap_client_secret"])
            except configuration.APIError as error:
                assert error.status in (400, 401)
            else:
                raise AssertionError("Temporary bootstrap access must be revoked")
            run("docker", "restart", keycloak)
            ports = json.loads(run("docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", keycloak))
            base = "http://127.0.0.1:" + ports["8080/tcp"][0]["HostPort"]
            health = "http://127.0.0.1:" + ports["9000/tcp"][0]["HostPort"] + "/health/ready"
            api = configuration.API(base)
            wait_ready(health)
            configuration.configure(api, realm, credentials)
            with urlopen(base + "/realms/platform/.well-known/openid-configuration") as response:
                discovery = json.load(response)
            assert discovery["issuer"] == "https://auth.damircloud.com/realms/platform"
            print("Passed: optimized startup, database isolation, realm setup, bootstrap revocation, password/access continuity and restart.")
        except Exception:
            logs = subprocess.run(["docker", "logs", "--tail", "35", keycloak], capture_output=True, text=True)
            sanitized = logs.stdout + logs.stderr
            for value in [admin_password, *(credentials[field] for field in prepare.SECRET_FIELDS)]:
                sanitized = sanitized.replace(value, "[redacted]")
            print(sanitized)
            raise
        finally:
            for name in (keycloak, postgres):
                subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["docker", "volume", "rm", volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
