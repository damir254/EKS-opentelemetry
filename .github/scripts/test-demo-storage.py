#!/usr/bin/env python3
"""Verify data survives replacing the pinned demo containers on the same volumes."""

import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import unittest
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]


def run(*args, input=None, timeout=120):
    result = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Never print environment files, database credentials or container logs.
        raise RuntimeError("Storage test operation failed: " + " ".join(args[:3]))
    return result.stdout.strip()


class DemoStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        application = yaml.safe_load((ROOT / "platform/argocd/applications/otel-demo.yaml").read_text())
        chart = ROOT / application["spec"]["source"]["path"]
        command = ["helm", "template", "otel-demo", str(chart), "--namespace", "dev"]
        for filename in application["spec"]["source"]["helm"]["valueFiles"]:
            command += ["--values", str(chart / filename)]
        cls.resources = [item for item in yaml.safe_load_all(run(*command)) if item]

    def setUp(self):
        self.prefix = "demo-storage-" + uuid.uuid4().hex[:12]
        self.containers = []
        self.volumes = []
        self.directory = tempfile.TemporaryDirectory(prefix=self.prefix)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.cleanup_docker)
        run("docker", "network", "create", "--internal", self.prefix)

    def cleanup_docker(self):
        for container in reversed(self.containers):
            subprocess.run(["docker", "rm", "--force", "--volumes", container], capture_output=True, timeout=30)
        for volume in self.volumes:
            subprocess.run(["docker", "volume", "rm", volume], capture_output=True, timeout=30)
        subprocess.run(["docker", "network", "rm", self.prefix], capture_output=True, timeout=30)

    def resource(self, kind, name):
        return next(item for item in self.resources if item["kind"] == kind and item["metadata"]["name"] == name)

    def configure(self, name):
        self.name = name
        self.pod = self.resource("StatefulSet", name)["spec"]["template"]["spec"]
        self.container = self.pod["containers"][0]
        mount = next(item for item in self.container["volumeMounts"] if item["name"] == "data")
        self.mount = mount["mountPath"]
        self.volume = self.prefix + "-data"
        self.volumes.append(self.volume)
        run("docker", "volume", "create", self.volume)
        environment = []
        for item in self.container.get("env", []):
            value = item.get("value", secrets.token_hex(24))
            environment.append(item["name"] + "=" + value)
        self.env_file = Path(self.directory.name) / "environment"
        self.env_file.write_text("\n".join(environment) + "\n")
        self.env_file.chmod(0o600)
        if name == "astronomy-db":
            self.init_file = Path(self.directory.name) / "init.sh"
            self.init_file.write_text(self.resource("ConfigMap", "astronomy-db-init")["data"]["init.sh"])
            self.init_file.chmod(0o755)
        if name == "kafka":
            # Docker has no fsGroup setting; mimic kubelet's group ownership on
            # a fresh, root-owned filesystem, rather than using image copy-up.
            group = str(self.pod["securityContext"]["fsGroup"])
            run("docker", "run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "sh",
                "--mount", self.data_mount(), self.container["image"], "-c",
                'chown "0:$1" "$2" && chmod 2770 "$2"', "sh", group, self.mount)

    def data_mount(self):
        return f"type=volume,src={self.volume},dst={self.mount},volume-nocopy"

    def start(self):
        self.running = self.prefix + "-" + str(len(self.containers))
        self.containers.append(self.running)
        args = ["docker", "run", "--detach", "--name", self.running, "--network", self.prefix,
                "--network-alias", self.name, "--env-file", str(self.env_file), "--mount", self.data_mount()]
        security = self.pod.get("securityContext", {})
        if "runAsUser" in security:
            args += ["--user", f"{security['runAsUser']}:{security['runAsGroup']}"]
        if self.name == "astronomy-db":
            args += ["--mount", f"type=bind,src={self.init_file},dst=/docker-entrypoint-initdb.d/init.sh,readonly"]
        # Apply the production memory ceiling while allowing CI CPU contention.
        args += ["--memory", self.container["resources"]["limits"]["memory"].replace("Mi", "m")]
        run(*args, self.container["image"], *self.container.get("args", []))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if self.name == "astronomy-db":
                    self.sql("SELECT count(*) FROM catalog.products;")
                elif self.name == "kafka":
                    self.kafka("kafka-topics.sh", "--list")
                else:
                    self.assertEqual(self.exec("valkey-cli", "ping"), "PONG")
                return
            except RuntimeError:
                state = json.loads(run("docker", "inspect", self.running))[0]["State"]
                if not state["Running"]:
                    raise RuntimeError(f"{self.name} exited during startup; OOMKilled={state['OOMKilled']}")
                time.sleep(0.5)
        raise RuntimeError(self.name + " did not become ready")

    def replace(self):
        run("docker", "stop", "--time", str(self.pod["terminationGracePeriodSeconds"]), self.running)
        run("docker", "rm", "--volumes", self.running)
        self.start()

    def exec(self, *args, input=None):
        return run("docker", "exec", "--interactive", self.running, *args, input=input)

    def sql(self, query):
        # TCP avoids connecting to the entrypoint's temporary init-only server.
        return self.exec("sh", "-c", 'PGPASSWORD="$POSTGRES_PASSWORD" exec "$@"', "sh",
                         "psql", "--host", "127.0.0.1", "--username", "postgres", "--dbname", "astronomy_db",
                         "--no-psqlrc", "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1", input=query)

    def kafka(self, tool, *args, input=None):
        return run("docker", "exec", "--interactive", "--env", "KAFKA_HEAP_OPTS=-Xms32M -Xmx96M",
                   self.running, "/opt/kafka/bin/" + tool, "--bootstrap-server", "kafka:9092", *args, input=input)

    def test_postgres_data_and_initialization_survive_replacement(self):
        self.configure("astronomy-db")
        self.start()
        initial_products = self.sql("SELECT count(*) FROM catalog.products;")
        self.sql("CREATE TABLE accounting.persistence_check (value text); INSERT INTO accounting.persistence_check VALUES ('retained');")
        self.replace()
        self.assertEqual(self.sql("SELECT value FROM accounting.persistence_check;"), "retained")
        self.assertEqual(self.sql("SELECT count(*) FROM catalog.products;"), initial_products)

    def test_kafka_messages_metadata_and_offsets_survive_replacement(self):
        self.configure("kafka")
        self.start()
        self.kafka("kafka-topics.sh", "--create", "--topic", "persistence-check", "--partitions", "1", "--replication-factor", "1")
        self.kafka("kafka-console-producer.sh", "--topic", "persistence-check", input="retained\n")
        group_args = ["--group", "persistence-check"]
        self.kafka("kafka-consumer-groups.sh", *group_args, "--reset-offsets", "--to-latest", "--execute", "--topic", "persistence-check")
        before = self.kafka("kafka-consumer-groups.sh", *group_args, "--describe")
        self.assertEqual(before.splitlines()[-1].split()[3:6], ["1", "1", "0"])
        metadata = self.exec("cat", self.mount + "/metadata/meta.properties")
        self.replace()
        self.assertEqual(self.exec("cat", self.mount + "/metadata/meta.properties"), metadata)
        self.assertEqual(self.kafka("kafka-console-consumer.sh", "--topic", "persistence-check", "--from-beginning",
                                    "--max-messages", "1", "--timeout-ms", "10000"), "retained")
        after = self.kafka("kafka-consumer-groups.sh", *group_args, "--describe")
        self.assertEqual(after.splitlines()[-1].split()[3:6], ["1", "1", "0"])

    def test_valkey_aof_survives_abrupt_container_replacement(self):
        self.configure("valkey-cart")
        self.start()
        self.assertEqual(self.exec("valkey-cli", "SET", "cart:persistence-check", "retained"), "OK")
        # Ensure this acknowledged write has been fsynced before simulating failure.
        self.assertEqual(self.exec("valkey-cli", "WAITAOF", "1", "0", "5000"), "1\n0")
        self.assertIn("aof_enabled:1", self.exec("valkey-cli", "INFO", "persistence"))
        run("docker", "kill", self.running)
        run("docker", "rm", "--volumes", self.running)
        self.start()
        self.assertEqual(self.exec("valkey-cli", "GET", "cart:persistence-check"), "retained")


if __name__ == "__main__":
    unittest.main(verbosity=2)
