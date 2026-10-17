# Recommendation service

Provides product recommendations over gRPC using the Product Catalog service.
It excludes products already supplied by the caller, limits the result set and
supports the demo's recommendation-cache failure flag.

Runtime settings, including the listening port, Product Catalog address, Flagd
and Collector endpoints, are in
[recommendation.yaml](../../helm/otel-demo/services/recommendation.yaml).

## Tests

Use the Python version in [.python-version](.python-version). From this directory,
create and activate a virtual environment, then install dependencies and run tests:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.txt
python -m pip install -r requirements-test.txt
pytest
```

The tests cover recommendations, errors, feature flags and caching. A startup test
also runs real local gRPC requests and checks exported telemetry against controlled
local dependencies. The tests do not require AWS or a live cluster.

## Container and telemetry

From the repository root:

```sh
docker build --platform linux/amd64 -f src/recommendation/Dockerfile -t recommendation:local .
```

The image starts through `opentelemetry-instrument` so requests, downstream calls,
custom metrics and logs use the configured telemetry providers. Health-check RPCs
are excluded from request telemetry. Recommendation CI runs the service tests
before building and scanning the image.
