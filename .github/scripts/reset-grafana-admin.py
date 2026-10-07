"""Reset the existing Grafana account to its AWS-backed Kubernetes Secret."""

import base64
import json
import subprocess
import sys


NAMESPACE = "monitoring"
SECRET = "monitoring-grafana-admin"
DEPLOYMENT = "monitoring-grafana"


def kubectl(stage, args, input_text=None):
    result = subprocess.run(["kubectl", "--namespace", NAMESPACE, "--request-timeout=20s", *args],
                            input=input_text, capture_output=True, text=True, timeout=90)
    if result.returncode:
        # Never print command output: secret reads and CLI diagnostics can be private.
        raise RuntimeError(f"{stage} failed; check cluster access and Grafana readiness")
    return result.stdout


def reset():
    external = json.loads(kubectl("Read ExternalSecret", ["get", "externalsecret", SECRET, "-o", "json"]))
    if not any(condition.get("type") == "Ready" and condition.get("status") == "True"
               for condition in external.get("status", {}).get("conditions", [])):
        raise RuntimeError("The AWS-backed admin Secret is not ready; sync monitoring first")
    deployment = json.loads(kubectl("Read Deployment", ["get", "deployment", DEPLOYMENT, "-o", "json"]))
    pod = deployment["spec"]["template"]["spec"]
    grafana = next(container for container in pod["containers"] if container["name"] == "grafana")
    password_env = next(env for env in grafana["env"] if env["name"] == "GF_SECURITY_ADMIN_PASSWORD")
    if password_env.get("valueFrom", {}).get("secretKeyRef", {}).get("name") != SECRET:
        raise RuntimeError("Grafana still uses the generated Secret; push and sync the configuration first")
    status = deployment.get("status", {})
    replicas = deployment["spec"].get("replicas", 1)
    if (status.get("observedGeneration") != deployment["metadata"]["generation"]
            or status.get("updatedReplicas", 0) != replicas
            or status.get("readyReplicas", 0) != replicas
            or status.get("replicas", 0) != replicas):
        raise RuntimeError("Wait for the Grafana rollout to complete before resetting the password")

    data = json.loads(kubectl("Read admin Secret", ["get", "secret", SECRET, "-o", "json"]))["data"]
    username = base64.b64decode(data["admin-user"], validate=True).decode()
    password = base64.b64decode(data["admin-password"], validate=True).decode()
    if username != "admin" or not password or password != password.strip() or any(c in password for c in "\x00\n\r"):
        raise RuntimeError("Unexpected admin Secret contents; reset stopped")

    execute = ["exec", "-i", f"deployment/{DEPLOYMENT}", "-c", "grafana", "--"]
    output = kubectl("Reset admin password", execute + [
        "grafana", "cli", "--homepath", "/usr/share/grafana", "--config", "/etc/grafana/grafana.ini",
        "admin", "reset-admin-password", "--password-from-stdin",
    ], password + "\n")
    if "admin password changed successfully" not in output.lower():
        raise RuntimeError("Grafana did not confirm the password reset")

    # Verify through Grafana's real login handler. No password in argv or output.
    http_status = kubectl("Verify login", execute + [
        "curl", "--silent", "--show-error", "--connect-timeout", "5", "--max-time", "10",
        "--header", "Content-Type: application/json", "--data-binary", "@-", "--output", "/dev/null",
        "--write-out", "%{http_code}", "http://127.0.0.1:3000/login",
    ], json.dumps({"user": username, "password": password}))
    if http_status.strip() != "200":
        raise RuntimeError("Password reset completed, but login verification failed; check login protection")
    print("Grafana admin password reset; login verified. Read monitoring-grafana-admin for access.")


if __name__ == "__main__":
    try:
        reset()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    except (ValueError, KeyError, TypeError, StopIteration, OSError, subprocess.TimeoutExpired):
        raise SystemExit("Grafana admin recovery failed; check the synced configuration and cluster access") from None
