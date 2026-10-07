#!/usr/bin/env python3
"""Create an encrypted shared-database snapshot or prepare its next restoration."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def aws(*args):
    result = subprocess.run(["aws", "rds", *args, "--output", "json"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("AWS RDS operation failed; check authentication, permissions and instance/snapshot status")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def latest_snapshot(snapshots, instance):
    candidates = [item for item in snapshots if item["DBInstanceIdentifier"] == instance
                  and item["Status"] == "available" and item["Engine"] == "postgres"
                  and item.get("Encrypted") is True]
    if not candidates:
        raise ValueError("No available encrypted snapshot exists; refusing to prepare an empty database")
    return max(candidates, key=lambda item: item["SnapshotCreateTime"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["snapshot", "restore"])
    parser.add_argument("--instance", default="otel-demo-eks-grafana")
    parser.add_argument("--snapshot", help="Restore this snapshot instead of the latest available manual snapshot")
    options = parser.parse_args()
    if options.action == "snapshot":
        if options.snapshot:
            parser.error("--snapshot is for restoration only")
        instance = aws("describe-db-instances", "--db-instance-identifier", options.instance)["DBInstances"][0]
        if instance["DBInstanceStatus"] != "available" or not instance["StorageEncrypted"]:
            raise ValueError("Snapshot source must be an available encrypted database")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        name = options.instance + "-sso-" + stamp
        aws("create-db-snapshot", "--db-instance-identifier", options.instance,
            "--db-snapshot-identifier", name,
            "--tags", "Key=Project,Value=EKS-opentelemetry", "Key=Purpose,Value=platform-identity-recovery")
        aws("wait", "db-snapshot-available", "--db-snapshot-identifier", name)
        snapshot = aws("describe-db-snapshots", "--db-snapshot-identifier", name)["DBSnapshots"][0]
        latest_snapshot([snapshot], options.instance)
        print("Verified encrypted snapshot: " + name)
    else:
        # Describe without the instance filter so a missing instance is not an API error.
        instances = aws("describe-db-instances")["DBInstances"]
        if any(item["DBInstanceIdentifier"] == options.instance for item in instances):
            raise ValueError("The database still exists; restoration is only for recreation after teardown")
        args = ["describe-db-snapshots", "--db-instance-identifier", options.instance, "--snapshot-type", "manual"]
        if options.snapshot:
            args = ["describe-db-snapshots", "--db-snapshot-identifier", options.snapshot]
        snapshot = latest_snapshot(aws(*args)["DBSnapshots"], options.instance)
        destination = ROOT / "terraform/database-restore.auto.tfvars.json"
        destination.write_text(json.dumps({"grafana_db_snapshot_identifier": snapshot["DBSnapshotArn"]}, indent=2) + "\n")
        print("Prepared restoration from " + snapshot["DBSnapshotIdentifier"] + "; review and apply Terraform next.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError) as error:
        raise SystemExit(str(error)) from None
