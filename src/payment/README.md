# Payment service

Simulates payment processing over gRPC. It validates card details, returns a
transaction ID, supports the demo failure flag and emits OpenTelemetry data.
It does not connect to a real payment provider.

The service also handles graceful shutdown: it stops advertising readiness,
allows active requests to finish and bounds how long shutdown can wait.

## Tests

Use the Node.js version in [.node-version](.node-version). From this directory:

```sh
npm ci
npm test
```

Tests cover payment validation, failure flags, telemetry behavior and request
draining during shutdown. They do not require the full demo environment.

## Container and deployment

From the repository root:

```sh
docker build --platform linux/amd64 -f src/payment/Dockerfile -t payment:local .
```

The build includes the shared protobuf definition. Deployment settings, including
the gRPC port, Flagd and Collector endpoints, are in
[payment.yaml](../../helm/otel-demo/services/payment.yaml).
Payment CI tests this service before calling the shared image workflow.
Argo CD deploys published versions using Argo Rollouts and Istio canary routing.
