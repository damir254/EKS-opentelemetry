#!/usr/bin/env python3
"""Exercise real PostgreSQL/Keycloak startup, isolation and safe reconciliation."""

import base64
import hashlib
from html.parser import HTMLParser
from http.cookiejar import CookieJar, DefaultCookiePolicy
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener, urlopen
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


class AuthPage(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.current = {"action": attributes.get("action"), "fields": {}}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and attributes.get("name"):
            self.current["fields"][attributes["name"]] = attributes.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class Browser:
    def __init__(self, base):
        self.base = base
        browser = self

        class LocalRedirects(HTTPRedirectHandler):
            def redirect_request(self, request, response, code, message, headers, newurl):
                return super().redirect_request(request, response, code, message, headers, browser.local_url(newurl))

        # Only the isolated Docker test uses HTTP loopback for HTTPS-hostname cookies.
        cookies = CookieJar(DefaultCookiePolicy(secure_protocols=("https", "http")))
        self.opener = build_opener(HTTPCookieProcessor(cookies), LocalRedirects())

    def local_url(self, url):
        parsed = urlsplit(url)
        if parsed.netloc == "auth.damircloud.com":
            return self.base + parsed.path + ("?" + parsed.query if parsed.query else "")
        if url.startswith(self.base + "/"):
            return url
        raise AssertionError("Authentication redirected outside Keycloak before MFA")

    def page(self, url, fields=None):
        data = urlencode(fields).encode() if fields is not None else None
        with self.opener.open(Request(self.local_url(url), data=data), timeout=15) as response:
            assert response.status == 200, "Browser authentication must return a usable page"
            return AuthPage(response.read().decode())

    def login_form(self, client):
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        parameters = {"client_id": client["clientId"], "redirect_uri": client["redirectUris"][0],
                      "response_type": "code", "scope": "openid profile email groups",
                      "state": secrets.token_urlsafe(32), "code_challenge": challenge,
                      "code_challenge_method": "S256"}
        page = self.page(self.base + "/realms/platform/protocol/openid-connect/auth?" + urlencode(parameters))
        form = next((item for item in page.forms if {"username", "password"} <= item["fields"].keys()), None)
        assert form is not None, "Fresh login must show username/password before OTP"
        return form


def check_login_pages(base, realm):
    for client in realm["clients"]:
        Browser(base).login_form(client)


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
            check_login_pages(base, realm)
            # Reproduce the old partial PUTs, then prove reconciliation repairs them.
            otp_index = next(index for index, item in enumerate(flow) if item.get("providerId") == "auth-otp-form")
            mfa = next(flow[index] for index in range(otp_index - 1, -1, -1)
                       if flow[index].get("authenticationFlow") and flow[index]["level"] == flow[otp_index]["level"] - 1)
            flow_path = "/admin/realms/platform/authentication/flows/platform-browser/executions"
            for execution in (mfa, flow[otp_index]):
                api.request("PUT", flow_path, {"id": execution["id"], "requirement": "REQUIRED"})
            try:
                check_login_pages(base, realm)
            except HTTPError as error:
                assert error.code == 400, "The legacy ordering bug must reproduce the login failure"
            else:
                raise AssertionError("Regression test did not reproduce the legacy login failure")
            configuration.configure(api, realm, credentials)
            check_login_pages(base, realm)
            users = api.request("GET", "/admin/realms/platform/users?username=operator&exact=true")
            user_id = users[0]["id"]
            groups = api.request("GET", f"/admin/realms/platform/users/{user_id}/groups")
            assert [group["name"] for group in groups] == ["platform-admins"]
            group_id = groups[0]["id"]
            changed_password = secrets.token_urlsafe(32)
            api.request("PUT", f"/admin/realms/platform/users/{user_id}/reset-password",
                        {"type": "password", "value": changed_password, "temporary": False})
            api.request("PUT", f"/admin/realms/platform/users/{user_id}", {
                "requiredActions": [], "firstName": "Test", "lastName": "Operator",
                "email": "operator@example.invalid", "emailVerified": True,
            })
            browser = Browser(base)
            form = browser.login_form(next(item for item in realm["clients"] if item["clientId"] == "grafana"))
            otp_page = browser.page(form["action"], {**form["fields"], "username": "operator", "password": changed_password})
            assert any({"totp", "totpSecret"} <= item["fields"].keys() for item in otp_page.forms), \
                "A valid password must lead to mandatory OTP enrollment even without seeded required actions"
            password_id = next(item["id"] for item in api.request("GET", f"/admin/realms/platform/users/{user_id}/credentials")
                               if item["type"] == "password")
            api.request("DELETE", f"/admin/realms/platform/users/{user_id}/groups/{group_id}")
            configuration.configure(api, realm, credentials)
            check_login_pages(base, realm)
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
            check_login_pages(base, realm)
            with urlopen(base + "/realms/platform/.well-known/openid-configuration") as response:
                discovery = json.load(response)
            assert discovery["issuer"] == "https://auth.damircloud.com/realms/platform"
            print("Passed: optimized startup, database isolation, browser login/OTP enrollment, legacy flow repair, bootstrap revocation, password/access continuity and restart.")
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
