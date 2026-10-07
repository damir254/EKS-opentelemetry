"""Reconcile SSO configuration without resetting existing users or logging credentials."""

import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class APIError(Exception):
    def __init__(self, status):
        self.status = status


class API:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.token = None

    def request(self, method, path, payload=None, form=False):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = None
        if payload is not None:
            data = urlencode(payload).encode() if form else json.dumps(payload).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        try:
            with urlopen(Request(self.base + path, data=data, headers=headers, method=method), timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as error:
            # Response bodies can include supplied secrets: discard them.
            raise APIError(error.code) from None

    def authenticate(self, realm, client, secret):
        self.token = None
        token = self.request("POST", f"/realms/{realm}/protocol/openid-connect/token", {
            "grant_type": "client_credentials", "client_id": client, "client_secret": secret,
        }, form=True)
        self.token = token["access_token"]


def find_client(api, realm, client_id):
    matches = api.request("GET", f"/admin/realms/{realm}/clients?" + urlencode({"clientId": client_id}))
    return next((item for item in matches if item["clientId"] == client_id), None)


def ensure_client(api, realm, representation):
    client = find_client(api, realm, representation["clientId"])
    if client:
        api.request("PUT", f'/admin/realms/{realm}/clients/{client["id"]}', representation)
    else:
        api.request("POST", f"/admin/realms/{realm}/clients", representation)
        client = find_client(api, realm, representation["clientId"])
    return client["id"]


def ensure_user(api, realm, username, password):
    users = api.request("GET", f"/admin/realms/{realm}/users?" + urlencode({"username": username, "exact": "true"}))
    user = next((item for item in users if item["username"] == username), None)
    if user:
        # Passwords, MFA devices and profile changes belong to the user, not Git.
        return user["id"], False
    api.request("POST", f"/admin/realms/{realm}/users", {
        "username": username, "enabled": True,
        "requiredActions": ["UPDATE_PASSWORD", "CONFIGURE_TOTP"],
        "credentials": [{"type": "password", "value": password, "temporary": True}],
    })
    users = api.request("GET", f"/admin/realms/{realm}/users?" + urlencode({"username": username, "exact": "true"}))
    return next(item["id"] for item in users if item["username"] == username), True


def enforce_mfa(api, realm):
    # Clone the built-in browser flow instead of changing Keycloak defaults.
    # Required OTP also handles existing accounts whose authenticator is removed.
    alias = "platform-browser"
    flows = api.request("GET", f"/admin/realms/{realm}/authentication/flows")
    if not any(flow["alias"] == alias for flow in flows):
        api.request("POST", f"/admin/realms/{realm}/authentication/flows/browser/copy", {"newName": alias})
    path = f"/admin/realms/{realm}/authentication/flows/{alias}/executions"
    executions = api.request("GET", path)
    otp_index = next(index for index, execution in enumerate(executions)
                     if execution.get("providerId") == "auth-otp-form")
    otp = executions[otp_index]
    parent_index = next(index for index in range(otp_index - 1, -1, -1)
                        if executions[index].get("authenticationFlow")
                        and executions[index]["level"] == otp["level"] - 1)
    parent = executions[parent_index]
    api.request("PUT", path, {"id": parent["id"], "requirement": "REQUIRED"})
    api.request("PUT", path, {"id": otp["id"], "requirement": "REQUIRED"})
    for execution in executions[parent_index + 1:]:
        if execution["level"] < otp["level"]:
            break
        if execution.get("providerId", "").startswith("conditional-"):
            api.request("PUT", path, {"id": execution["id"], "requirement": "DISABLED"})
    api.request("PUT", f"/admin/realms/{realm}", {"browserFlow": alias})


def configure(api, settings, credentials):
    realm = settings["realm"]
    if realm != "platform":
        raise ValueError("Unexpected application realm")
    temporary = False
    try:
        api.authenticate(realm, "platform-configurator", credentials["configuration_client_secret"])
        api.request("GET", f"/admin/realms/{realm}")
    except APIError as error:
        if error.status not in (400, 401, 403, 404):
            raise
        api.authenticate("master", "platform-bootstrap", credentials["bootstrap_client_secret"])
        temporary = True

    nested = {"clients", "groups", "requiredActions"}
    realm_settings = {key: value for key, value in settings.items() if key not in nested}
    try:
        api.request("GET", f"/admin/realms/{realm}")
    except APIError as error:
        if error.status != 404:
            raise
        api.request("POST", "/admin/realms", realm_settings)
    api.request("PUT", f"/admin/realms/{realm}", realm_settings)

    for action in settings["requiredActions"]:
        api.request("PUT", f'/admin/realms/{realm}/authentication/required-actions/{action["alias"]}', action)
    enforce_mfa(api, realm)

    existing_groups = api.request("GET", f"/admin/realms/{realm}/groups")
    for group in settings["groups"]:
        if not any(item["name"] == group["name"] for item in existing_groups):
            api.request("POST", f"/admin/realms/{realm}/groups", group)

    scopes = api.request("GET", f"/admin/realms/{realm}/client-scopes")
    scope = next((item for item in scopes if item["name"] == "groups"), None)
    if not scope:
        api.request("POST", f"/admin/realms/{realm}/client-scopes", {"name": "groups", "protocol": "openid-connect"})
        scopes = api.request("GET", f"/admin/realms/{realm}/client-scopes")
        scope = next(item for item in scopes if item["name"] == "groups")
    mapper = {"name": "groups", "protocol": "openid-connect", "protocolMapper": "oidc-group-membership-mapper",
              "config": {"claim.name": "groups", "full.path": "false", "id.token.claim": "true",
                         "access.token.claim": "true", "userinfo.token.claim": "true"}}
    mapper_path = f'/admin/realms/{realm}/client-scopes/{scope["id"]}/protocol-mappers/models'
    existing = api.request("GET", mapper_path)
    current = next((item for item in existing if item["name"] == "groups"), None)
    api.request("PUT" if current else "POST", mapper_path + ("/" + current["id"] if current else ""), mapper)
    for source in settings["clients"]:
        representation = dict(source)
        if representation["clientId"] == "grafana":
            representation["secret"] = credentials["grafana_client_secret"]
        client_id = ensure_client(api, realm, representation)
        api.request("PUT", f'/admin/realms/{realm}/clients/{client_id}/default-client-scopes/{scope["id"]}')

    # Seed human accounts only while temporary bootstrap access is active.
    # Later syncs must not recreate deleted operators or restore revoked access.
    if temporary:
        user_id, created = ensure_user(api, realm, credentials["operator_username"], credentials["operator_password"])
        groups = api.request("GET", f"/admin/realms/{realm}/groups")
        group = next(item for item in groups if item["name"] == "platform-admins")
        api.request("PUT", f'/admin/realms/{realm}/users/{user_id}/groups/{group["id"]}')
        manager_id = ensure_client(api, realm, {
            "clientId": "platform-configurator", "enabled": True, "protocol": "openid-connect",
            "publicClient": False, "serviceAccountsEnabled": True,
            "fullScopeAllowed": False, "defaultClientScopes": ["roles"],
            "standardFlowEnabled": False, "directAccessGrantsEnabled": False,
            "secret": credentials["configuration_client_secret"],
        })
        service_user = api.request("GET", f"/admin/realms/{realm}/clients/{manager_id}/service-account-user")
        management = find_client(api, realm, "realm-management")
        roles = api.request("GET", f'/admin/realms/{realm}/clients/{management["id"]}/roles')
        allowed = {"manage-realm", "manage-clients", "manage-users", "view-realm", "view-clients", "query-users"}
        permitted = [role for role in roles if role["name"] in allowed]
        api.request("POST", f'/admin/realms/{realm}/users/{service_user["id"]}/role-mappings/clients/{management["id"]}',
                    permitted)
        api.request("POST", f'/admin/realms/{realm}/clients/{manager_id}/scope-mappings/clients/{management["id"]}', permitted)
        admin_id, admin_created = ensure_user(api, "master", credentials["admin_username"], credentials["admin_password"])
        if admin_created or temporary:
            admin_role = api.request("GET", "/admin/realms/master/roles/admin")
            api.request("POST", f"/admin/realms/master/users/{admin_id}/role-mappings/realm", [admin_role])
        # Prove that the restricted permanent client works before revoking bootstrap.
        bootstrap = find_client(api, "master", "platform-bootstrap")
        original_token = api.token
        api.authenticate(realm, "platform-configurator", credentials["configuration_client_secret"])
        api.request("GET", f"/admin/realms/{realm}")
        api.token = original_token
        api.request("DELETE", f'/admin/realms/master/clients/{bootstrap["id"]}')


def main():
    credentials = {path.name: path.read_text() for path in Path(sys.argv[1]).iterdir()
                   if path.is_file() and not path.name.startswith(".")}
    settings = json.loads(Path(sys.argv[2]).read_text())
    api = API(os.environ.get("KEYCLOAK_URL", "http://keycloak.identity.svc.cluster.local:8080"))
    for attempt in range(30):
        try:
            configure(api, settings, credentials)
            print("Keycloak realm, clients, MFA policy and group mappings are ready.")
            return
        except (APIError, URLError, OSError) as error:
            # Retry connection/startup/transient errors, never credential/permission failures.
            if isinstance(error, APIError) and error.status not in (409, 429, 500, 502, 503, 504):
                raise
            time.sleep(5)
    raise RuntimeError("Keycloak provisioning did not become ready")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Keycloak configuration failed; check database readiness, API permissions and secret continuity") from None
