"""Enable required main-branch checks after the new CI workflow is pushed."""

import argparse
import json
import subprocess
import tempfile


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="damir254/EKS-opentelemetry")
    parser.add_argument("--deploy-key-title", default="Argo CD Image Updater EKS")
    args = parser.parse_args()
    base = f"repos/{args.repo}"
    # Do not block the initial push by requiring checks that do not exist yet.
    api(base + "/contents/.github/workflows/ci-infrastructure.yaml?ref=main")
    # GitHub's DeployKey bypass applies to every deploy key, not an individual ID.
    # Only enable it when Image Updater owns the repository's sole writable key.
    keys = [k for k in api(base + "/keys") if not k["read_only"]]
    if len(keys) != 1 or keys[0]["title"] != args.deploy_key_title:
        raise SystemExit("Expected Image Updater to own the sole writable deploy key; inspect repository keys")
    app_id = api("apps/github-actions")["id"]
    name = "Required infrastructure CI"
    rules = {
        "name": name, "target": "branch", "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        # Existing GitOps write-back remains possible; human merges require CI.
        "bypass_actors": [{"actor_id": None, "actor_type": "DeployKey", "bypass_mode": "always"}],
        "rules": [{"type": "required_status_checks", "parameters": {
            "strict_required_status_checks_policy": True,
            "do_not_enforce_on_create": True,
            "required_status_checks": [{"context": check, "integration_id": app_id} for check in
                                       ("Terraform validation", "Kubernetes and workflow validation")],
        }}],
    }
    existing = [r for r in api(base + "/rulesets") if r["name"] == name]
    if len(existing) > 1:
        raise SystemExit("Multiple matching rulesets; inspect them before updating")
    endpoint = base + "/rulesets" + (f'/{existing[0]["id"]}' if existing else "")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as body:
        json.dump(rules, body)
        body.flush()
        subprocess.run(["gh", "api", "--method", "PUT" if existing else "POST", endpoint,
                        "--input", body.name, "--silent"], check=True)
    print("Required infrastructure checks enabled; Image Updater deploy key retains GitOps write-back.")


if __name__ == "__main__":
    main()
