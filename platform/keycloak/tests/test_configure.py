"""Guard the browser flow against Keycloak PUTs resetting execution priority."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/configure.py"
spec = importlib.util.spec_from_file_location("keycloak_configure", SCRIPT)
configuration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configuration)


class FlowAPI:
    def __init__(self, existing=False, broken=False):
        self.existing = existing
        self.updates = []
        self.browser_flow = None
        self.executions = [
            self.execution("cookie", None, 10, "ALTERNATIVE", "auth-cookie"),
            self.execution("organization", None, 40, "ALTERNATIVE", flow=True),
            self.execution("organization-condition", "organization", 10, "REQUIRED", "conditional-user-configured"),
            self.execution("forms", None, 50, "ALTERNATIVE", flow=True),
            self.execution("password", "forms", 10, "REQUIRED", "auth-username-password-form"),
            self.execution("mfa", "forms", 20, "CONDITIONAL", flow=True),
            self.execution("user-condition", "mfa", 10, "REQUIRED", "conditional-user-configured"),
            self.execution("credential-condition", "mfa", 20, "REQUIRED", "conditional-credential"),
            self.execution("otp", "mfa", 30, "ALTERNATIVE", "auth-otp-form"),
        ]
        if broken:
            for execution in self.executions:
                if execution["id"] in ("mfa", "otp", "user-condition", "credential-condition"):
                    execution["priority"] = 0
                    execution["requirement"] = "DISABLED" if "condition" in execution["id"] else "REQUIRED"

    @staticmethod
    def execution(identity, parent, priority, requirement, provider=None, flow=False):
        return {"id": identity, "parent": parent, "priority": priority,
                "requirement": requirement, "providerId": provider, "authenticationFlow": flow}

    def flattened(self, parent=None, level=0):
        result = []
        siblings = sorted((item for item in self.executions if item["parent"] == parent),
                          key=lambda item: item["priority"])
        for execution in siblings:
            result.append({**deepcopy(execution), "level": level})
            result.extend(self.flattened(execution["id"], level + 1))
        return result

    def request(self, method, path, payload=None):
        if method == "GET" and path.endswith("/authentication/flows"):
            return [{"alias": "platform-browser"}] if self.existing else []
        if method == "POST" and path.endswith("/browser/copy"):
            self.existing = True
            return
        if method == "GET" and path.endswith("/executions"):
            return self.flattened()
        if method == "PUT" and path.endswith("/executions"):
            self.updates.append(deepcopy(payload))
            execution = next(item for item in self.executions if item["id"] == payload["id"])
            # Match the real server: priority is a primitive integer, defaulting to 0.
            execution.update(requirement=payload["requirement"], priority=payload.get("priority", 0))
            return
        if method == "PUT" and path == "/admin/realms/platform":
            self.browser_flow = payload["browserFlow"]
            return
        raise AssertionError("Unexpected authentication flow operation")


class KeycloakFlowTests(unittest.TestCase):
    def assert_password_before_mfa(self, api):
        executions = api.flattened()
        identities = [item["id"] for item in executions]
        self.assertLess(identities.index("password"), identities.index("mfa"))
        self.assertLess(identities.index("password"), identities.index("otp"))
        for identity in ("password", "mfa", "otp"):
            self.assertEqual(next(item for item in executions if item["id"] == identity)["requirement"], "REQUIRED")
        self.assertEqual(api.browser_flow, "platform-browser")

    def test_new_flow_preserves_priorities_and_only_disables_mfa_conditions(self):
        api = FlowAPI()
        priorities = {item["id"]: item["priority"] for item in api.executions}
        configuration.enforce_mfa(api, "platform")
        self.assert_password_before_mfa(api)
        self.assertEqual({item["id"]: item["priority"] for item in api.executions}, priorities)
        self.assertTrue(all("priority" in update for update in api.updates))
        for item in api.executions:
            if item["id"] in ("user-condition", "credential-condition"):
                self.assertEqual(item["requirement"], "DISABLED")
            if item["id"] == "organization-condition":
                self.assertEqual(item["requirement"], "REQUIRED")

    def test_existing_zero_priority_flow_is_repaired_and_repeat_sync_keeps_order(self):
        api = FlowAPI(existing=True, broken=True)
        self.assertLess([item["id"] for item in api.flattened()].index("otp"),
                        [item["id"] for item in api.flattened()].index("password"))
        configuration.enforce_mfa(api, "platform")
        self.assert_password_before_mfa(api)
        updates = len(api.updates)
        configuration.enforce_mfa(api, "platform")
        self.assert_password_before_mfa(api)
        self.assertEqual(len(api.updates), updates)

    def test_different_password_and_mfa_parent_flows_fail_before_updates(self):
        api = FlowAPI(existing=True)
        next(item for item in api.executions if item["id"] == "password")["parent"] = "organization"
        with self.assertRaises(ValueError):
            configuration.enforce_mfa(api, "platform")
        self.assertEqual(api.updates, [])


if __name__ == "__main__":
    unittest.main()
