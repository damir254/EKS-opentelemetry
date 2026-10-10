# Recommendation Service

This service provides recommendations for other products based on the currently
selected product.

## Docker Build

From the root directory, run:

```sh
docker build -f src/recommendation/Dockerfile -t recommendation:local .
```

## Telemetry

The image starts with `opentelemetry-instrument python recommendation_server.py`.
Pinned gRPC and logging instrumentations initialize the tracing, metric and logging
SDKs before application startup. Incoming RPCs produce SERVER spans; Product Catalog
calls produce CLIENT spans and propagate trace context. The app's custom spans,
recommendations counter and INFO logs use these same providers.

Deployment settings live in `helm/otel-demo/services/recommendation.yaml`:

- `OTEL_EXPORTER_OTLP_ENDPOINT` sends all signals to the existing Collector over OTLP/gRPC.
- `OTEL_PYTHON_GRPC_EXCLUDED_SERVICES=grpc.health.v1.Health` excludes readiness RPCs.
- `OTEL_PYTHON_LOG_AUTO_INSTRUMENTATION=true` attaches one OTLP logging handler.

The Collector converts SERVER spans into request, error and duration metrics used
by the existing RED dashboard, including p95/p99 latency. No additional Prometheus
scrape target is needed. Running the service directly outside the image also
requires the `opentelemetry-instrument` wrapper.

## Tests

With Python 3.13, from this directory:

```sh
python -m pip install --require-hashes -r requirements.txt
python -m pip install -r requirements-test.txt
pytest
```

The startup test uses the Dockerfile command and Helm environment defaults with
local gRPC Product Catalog, Flagd and OTLP receivers. It checks SERVER and CLIENT
spans, error status, health exclusions, custom metrics and correlated INFO logs
without requiring a live cluster.
