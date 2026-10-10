"""Verify dependency exporter metrics against the actual pinned server images."""

import importlib.util
from pathlib import Path
import re
import subprocess
import time
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("storage", ROOT / ".github/scripts/test-demo-storage.py")
storage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(storage)


class DependencyMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage.DemoStorageTests.setUpClass()
        cls.resources = storage.DemoStorageTests.resources
        cls.python_image = yaml.safe_load((ROOT / "platform/monitoring/database/values.yaml").read_text())["images"]["python"]

    def start(self, dependency, server):
        self.fixture = storage.DemoStorageTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.configure(server)
        self.fixture.start()
        deployment = next(r for r in self.resources if r["kind"] == "Deployment"
                          and r["metadata"]["name"] == dependency + "-exporter")
        pod = deployment["spec"]["template"]["spec"]
        exporter = pod["containers"][0]
        security = pod["securityContext"]
        name = self.fixture.prefix + "-exporter"
        self.fixture.containers.append(name)
        args = ["docker", "run", "--detach", "--name", name, "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", f"{security['runAsUser']}:{security['runAsGroup']}",
                "--memory", exporter["resources"]["limits"]["memory"].replace("Mi", "m"),
                "--network", self.fixture.prefix, "--network-alias", "exporter"]
        if dependency == "postgres":
            # Disposable credentials only; never print passwords or env files.
            environment = dict(line.split("=", 1) for line in self.fixture.env_file.read_text().splitlines())
            password = Path(self.fixture.directory.name) / "password"
            password.write_text(environment["MONITORING_PASSWORD"])
            password.chmod(0o644)  # Mimics a readable Kubernetes Secret volume.
            args += ["--mount", f"type=bind,src={password},dst=/etc/postgres-exporter/password,readonly"]
            for entry in exporter["env"]:
                args += ["--env", entry["name"] + "=" + entry["value"]]
        storage.run(*args, exporter["image"], *exporter["args"])
        self.port = exporter["ports"][0]["containerPort"]

    def scrape(self):
        script = ("from urllib.request import urlopen; "
                  f"print(urlopen('http://exporter:{self.port}/metrics',timeout=12).read().decode())")
        return storage.run("docker", "run", "--rm", "--read-only", "--network", self.fixture.prefix,
                           self.python_image, "python3", "-c", script, timeout=30)

    def values(self, metrics, name):
        return [float(match.group(1)) for line in metrics.splitlines()
                if (match := re.fullmatch(re.escape(name) + r'(?:\{.*\})? ([\d.eE+-]+)', line))]

    def wait_for(self, check, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                metrics = self.scrape()
                if check(metrics):
                    return metrics
            except RuntimeError:
                pass
            time.sleep(0.5)
        self.fail("Expected dependency metrics were not exposed")

    def test_postgres_monitoring_role_observes_connections_and_blocked_queries(self):
        self.start("postgres", "astronomy-db")
        metrics = self.wait_for(lambda text: self.values(text, "pg_up") == [1])
        for metric in ("pg_stat_database_numbackends", "pg_settings_max_connections",
                       "pg_stat_database_deadlocks", "pg_stat_activity_count",
                       "pg_stat_activity_max_tx_duration", "pg_locks_count"):
            self.assertTrue(self.values(metrics, metric), metric)
        # A real lock holder and waiter must be visible to the existing pg_monitor user.
        database = self.fixture.running
        command = ["docker", "exec", "--interactive", database, "psql", "--username", "postgres",
                   "--dbname", "astronomy_db", "--no-psqlrc", "--set", "ON_ERROR_STOP=1"]
        holder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        holder.stdin.write("BEGIN; LOCK TABLE catalog.products IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(60);\n")
        holder.stdin.close()
        self.addCleanup(lambda: holder.wait(timeout=10) if holder.poll() is not None else holder.kill())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.fixture.sql("SELECT count(*) FROM pg_locks WHERE mode='AccessExclusiveLock' AND granted;") != "0":
                break
            time.sleep(0.1)
        else:
            self.fail("Test lock was not acquired")
        waiter = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        waiter.stdin.write("SET statement_timeout='60s'; SELECT count(*) FROM catalog.products;\n")
        waiter.stdin.close()
        self.addCleanup(lambda: waiter.wait(timeout=10) if waiter.poll() is not None else waiter.kill())
        self.wait_for(lambda text: any('wait_event_type="Lock"' in line and float(line.rsplit(" ", 1)[1]) > 0
                                      for line in text.splitlines() if line.startswith("pg_stat_activity_count{")))
        self.fixture.sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid != pg_backend_pid() AND usename='postgres';")
        storage.run("docker", "stop", "--time", "5", database)
        self.wait_for(lambda text: self.values(text, "pg_up") == [0])

    def test_valkey_reports_cache_activity_and_backend_failure(self):
        self.start("valkey", "valkey-cart")
        self.wait_for(lambda text: self.values(text, "redis_up") == [1])
        self.fixture.exec("valkey-cli", "SET", "cart:monitoring-check", "value")
        self.fixture.exec("valkey-cli", "GET", "cart:monitoring-check")
        self.fixture.exec("valkey-cli", "GET", "cart:missing-check")
        metrics = self.wait_for(lambda text: sum(self.values(text, "redis_keyspace_hits_total")) > 0
                               and sum(self.values(text, "redis_keyspace_misses_total")) > 0)
        for metric in ("redis_memory_used_bytes", "redis_memory_used_rss_bytes", "redis_memory_max_bytes",
                       "redis_evicted_keys_total", "redis_rejected_connections_total",
                       "redis_commands_duration_seconds_total", "redis_aof_last_write_status"):
            self.assertTrue(self.values(metrics, metric), metric)
        self.assertEqual(self.values(metrics, "redis_aof_enabled"), [1])
        storage.run("docker", "stop", "--time", "5", self.fixture.running)
        self.wait_for(lambda text: self.values(text, "redis_up") == [0])

    def test_kafka_lag_tracks_real_committed_offsets_and_backlog(self):
        self.start("kafka", "kafka")
        self.fixture.kafka("kafka-topics.sh", "--create", "--topic", "orders", "--partitions", "2", "--replication-factor", "1")
        self.fixture.kafka("kafka-console-producer.sh", "--topic", "orders", input="order\n" * 20)
        group = ["--group", "monitoring-check", "--reset-offsets", "--execute", "--topic", "orders"]
        self.fixture.kafka("kafka-consumer-groups.sh", *group, "--to-earliest")
        metrics = self.wait_for(lambda text: sum(self.values(text, "kafka_consumergroup_lag")) == 20)
        self.assertEqual(self.values(metrics, "kafka_brokers"), [1])
        for metric in ("kafka_topic_partitions", "kafka_topic_partition_leader",
                       "kafka_topic_partition_under_replicated_partition", "kafka_consumergroup_current_offset"):
            self.assertTrue(self.values(metrics, metric), metric)
        self.fixture.kafka("kafka-consumer-groups.sh", *group, "--to-latest")
        self.wait_for(lambda text: self.values(text, "kafka_consumergroup_lag")
                      and sum(self.values(text, "kafka_consumergroup_lag")) == 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
