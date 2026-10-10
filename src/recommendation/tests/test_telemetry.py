# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
"""Exercise the production startup command and Helm telemetry settings over gRPC."""

import json
import os
from pathlib import Path
import socket
import subprocess
import threading
from concurrent import futures
from types import SimpleNamespace

import grpc
import pytest
import yaml
from grpc_health.v1 import health_pb2, health_pb2_grpc
from openfeature.schemas.protobuf.flagd.evaluation.v2 import (
    evaluation_pb2,
    evaluation_pb2_grpc,
)
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
from opentelemetry.proto.collector.metrics.v1 import (
    metrics_service_pb2,
    metrics_service_pb2_grpc,
)
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc
from opentelemetry.proto.metrics.v1.metrics_pb2 import AGGREGATION_TEMPORALITY_DELTA
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

import demo_pb2
import demo_pb2_grpc

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = SERVICE_DIR.parents[1]
RPC_NAME = "/oteldemo.RecommendationService/ListRecommendations"
POD_RESOURCE_ATTRIBUTES = {
    "service.instance.id": "11111111-1111-4111-8111-111111111111",
    "k8s.pod.uid": "11111111-1111-4111-8111-111111111111",
    "k8s.pod.name": "recommendation-test",
    "k8s.namespace.name": "dev",
    "k8s.node.name": "test-node",
}


class Telemetry:
    def __init__(self):
        self.condition = threading.Condition()
        self.requests = {"traces": [], "metrics": [], "logs": []}

    def add(self, signal, request):
        with self.condition:
            self.requests[signal].append(request)
            self.condition.notify_all()

    def records(self, signal):
        resource_field, scope_field, record_field = {
            "traces": ("resource_spans", "scope_spans", "spans"),
            "metrics": ("resource_metrics", "scope_metrics", "metrics"),
            "logs": ("resource_logs", "scope_logs", "log_records"),
        }[signal]
        with self.condition:
            return [
                (resource.resource, record)
                for request in self.requests[signal]
                for resource in getattr(request, resource_field)
                for scope in getattr(resource, scope_field)
                for record in getattr(scope, record_field)
            ]


class TraceReceiver(trace_service_pb2_grpc.TraceServiceServicer):
    def __init__(self, telemetry):
        self.telemetry = telemetry

    def Export(self, request, context):
        self.telemetry.add("traces", request)
        return trace_service_pb2.ExportTraceServiceResponse()


class MetricReceiver(metrics_service_pb2_grpc.MetricsServiceServicer):
    def __init__(self, telemetry):
        self.telemetry = telemetry

    def Export(self, request, context):
        self.telemetry.add("metrics", request)
        return metrics_service_pb2.ExportMetricsServiceResponse()


class LogReceiver(logs_service_pb2_grpc.LogsServiceServicer):
    def __init__(self, telemetry):
        self.telemetry = telemetry

    def Export(self, request, context):
        self.telemetry.add("logs", request)
        return logs_service_pb2.ExportLogsServiceResponse()


class Catalog(demo_pb2_grpc.ProductCatalogServiceServicer):
    def __init__(self):
        self.fail = False
        self.metadata = []

    def ListProducts(self, request, context):
        self.metadata.append(dict(context.invocation_metadata()))
        if self.fail:
            context.abort(grpc.StatusCode.UNAVAILABLE, "catalog unavailable")
        return demo_pb2.ListProductsResponse(
            products=[demo_pb2.Product(id=f"p{i}") for i in range(8)]
        )


class Flags(evaluation_pb2_grpc.ServiceServicer):
    def EventStream(self, request, context):
        stopped = threading.Event()
        context.add_callback(stopped.set)
        yield evaluation_pb2.EventStreamResponse(type="provider_ready")
        stopped.wait()

    def ResolveBoolean(self, request, context):
        return evaluation_pb2.ResolveBooleanResponse(value=False, reason="STATIC")


@pytest.fixture
def telemetry_app(tmp_path):
    telemetry = Telemetry()
    catalog = Catalog()
    backend = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    demo_pb2_grpc.add_ProductCatalogServiceServicer_to_server(catalog, backend)
    evaluation_pb2_grpc.add_ServiceServicer_to_server(Flags(), backend)
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(TraceReceiver(telemetry), backend)
    metrics_service_pb2_grpc.add_MetricsServiceServicer_to_server(MetricReceiver(telemetry), backend)
    logs_service_pb2_grpc.add_LogsServiceServicer_to_server(LogReceiver(telemetry), backend)
    backend_port = backend.add_insecure_port("127.0.0.1:0")
    backend.start()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        app_port = listener.getsockname()[1]

    # Consume the real deployment defaults and image startup command so either
    # losing instrumentation or removing the health exclusion fails this test.
    config = yaml.safe_load(
        (REPO_DIR / "helm/otel-demo/services/recommendation.yaml").read_text()
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("OTEL_")}
    env.update({
        key: str(value["value"])
        for key, value in config["services"]["recommendation"]["env"].items()
    })
    # Model the expanded pod metadata supplied by the shared Helm template;
    # the real SDK must prefer this UID over its automatic process identity.
    env["OTEL_RESOURCE_ATTRIBUTES"] += "," + ",".join(
        f"{key}={value}" for key, value in POD_RESOURCE_ATTRIBUTES.items()
    )
    env.update({
        "RECOMMENDATION_PORT": str(app_port),
        "PRODUCT_CATALOG_ADDR": f"127.0.0.1:{backend_port}",
        "FLAGD_HOST": "127.0.0.1",
        "FLAGD_PORT": str(backend_port),
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{backend_port}",
        "OTEL_BSP_SCHEDULE_DELAY": "100",
        "OTEL_BLRP_SCHEDULE_DELAY": "100",
        "OTEL_METRIC_EXPORT_INTERVAL": "100",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    entrypoint = next(
        line.removeprefix("ENTRYPOINT ")
        for line in (SERVICE_DIR / "Dockerfile").read_text().splitlines()
        if line.startswith("ENTRYPOINT ")
    )
    output_path = tmp_path / "recommendation.log"
    process = None
    channel = grpc.insecure_channel(f"127.0.0.1:{app_port}")
    try:
        with output_path.open("w") as output:
            process = subprocess.Popen(
                json.loads(entrypoint), cwd=SERVICE_DIR, env=env,
                stdout=output, stderr=subprocess.STDOUT,
            )
            try:
                grpc.channel_ready_future(channel).result(timeout=20)
            except grpc.FutureTimeoutError:
                pytest.fail(f"Recommendation did not start:\n{output_path.read_text()}")
            yield SimpleNamespace(
                telemetry=telemetry, catalog=catalog, channel=channel,
                output_path=output_path,
            )
    finally:
        channel.close()
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        backend.stop(0).wait(timeout=5)


def test_startup_exports_real_requests_metrics_and_correlated_info_logs(telemetry_app):
    app = telemetry_app
    health = health_pb2_grpc.HealthStub(app.channel)
    for _ in range(3):
        response = health.Check(health_pb2.HealthCheckRequest(), timeout=5)
        assert response.status == health_pb2.HealthCheckResponse.SERVING

    client = demo_pb2_grpc.RecommendationServiceStub(app.channel)
    incoming_trace_id = "0123456789abcdef0123456789abcdef"
    incoming_parent_id = "0123456789abcdef"
    response = client.ListRecommendations(
        demo_pb2.ListRecommendationsRequest(product_ids=["p0"]), timeout=5,
        metadata=(("traceparent", f"00-{incoming_trace_id}-{incoming_parent_id}-01"),),
    )
    assert len(response.product_ids) == 5
    assert "p0" not in response.product_ids
    app.catalog.fail = True
    with pytest.raises(grpc.RpcError) as failure:
        client.ListRecommendations(demo_pb2.ListRecommendationsRequest(), timeout=5)
    assert failure.value.code() == grpc.StatusCode.UNAVAILABLE

    def complete():
        server_spans = [
            span for _, span in app.telemetry.records("traces")
            if span.name == RPC_NAME and span.kind == Span.SPAN_KIND_SERVER
        ]
        return (
            len(server_spans) == 2
            and any(
                metric.name == "demo.recommendation.requests"
                for _, metric in app.telemetry.records("metrics")
            )
            and any(
                record.body.string_value.startswith("Receive ListRecommendations")
                for _, record in app.telemetry.records("logs")
            )
        )

    with app.telemetry.condition:
        assert app.telemetry.condition.wait_for(complete, timeout=15), app.output_path.read_text()

    spans = [span for _, span in app.telemetry.records("traces")]
    server_spans = [span for span in spans if span.kind == Span.SPAN_KIND_SERVER]
    assert len(server_spans) == 2  # Health checks must not enter RED metrics.
    assert all(
        span.name == RPC_NAME and span.end_time_unix_nano > span.start_time_unix_nano
        for span in server_spans
    )
    assert sum(span.status.code == Status.STATUS_CODE_ERROR for span in server_spans) == 1
    success = next(span for span in server_spans if span.status.code != Status.STATUS_CODE_ERROR)
    assert success.trace_id.hex() == incoming_trace_id
    assert success.parent_span_id.hex() == incoming_parent_id
    internal = next(
        span for span in spans
        if span.name == "get_product_list" and span.trace_id == success.trace_id
    )
    catalog_call = next(
        span for span in spans
        if span.kind == Span.SPAN_KIND_CLIENT
        and "ProductCatalogService/ListProducts" in span.name
        and span.trace_id == success.trace_id
    )
    assert internal.parent_span_id == success.span_id
    assert catalog_call.parent_span_id == internal.span_id
    assert app.catalog.metadata[0]["traceparent"].split("-")[1] == success.trace_id.hex()
    assert not any("grpc.health.v1.Health" in span.name for span in spans)

    metrics = [
        metric for _, metric in app.telemetry.records("metrics")
        if metric.name == "demo.recommendation.requests"
    ]
    assert all(
        metric.sum.aggregation_temporality == AGGREGATION_TEMPORALITY_DELTA
        for metric in metrics
    )
    assert sum(point.as_int for metric in metrics for point in metric.sum.data_points) == 5

    logs = [
        record for _, record in app.telemetry.records("logs")
        if record.body.string_value.startswith("Receive ListRecommendations")
    ]
    assert len(logs) == 1  # One handler; no competing manual logger provider.
    assert logs[0].severity_text == "INFO"
    assert logs[0].trace_id == success.trace_id
    assert logs[0].span_id == success.span_id
    for signal in ("traces", "metrics", "logs"):
        assert app.telemetry.records(signal)
        for resource, _ in app.telemetry.records(signal):
            attributes = {item.key: item.value.string_value for item in resource.attributes}
            assert attributes["service.name"] == "recommendation"
            assert attributes["service.namespace"] == "otel-demo"
            for key, value in POD_RESOURCE_ATTRIBUTES.items():
                assert attributes[key] == value
