# Product Catalog service

Provides gRPC endpoints to list, retrieve and search products stored in PostgreSQL.
It converts database rows to the demo's protobuf messages and emits telemetry for
requests and database queries.

The demo PostgreSQL initialization script creates the catalog data. The service
requires `DB_CONNECTION_STRING`; deployment supplies it through a Kubernetes
Secret backed by Secrets Manager. Runtime settings are in
[product-catalog.yaml](../../helm/otel-demo/services/product-catalog.yaml).

## Tests and local build

Use the Go version declared in [go.mod](go.mod). From this directory:

```sh
go test ./...
go build -o product-catalog .
```

The tests cover row conversion, required configuration and database-error
handling. They do not require a running database. Running the server itself
requires PostgreSQL, Flagd and the configured telemetry endpoints.

## Container build

From the repository root:

```sh
docker build --platform linux/amd64 -f src/product-catalog/Dockerfile -t product-catalog:local .
```

The Dockerfile compiles a Linux amd64 binary and packages it in a non-root
runtime image. Product Catalog CI runs the Go tests before building and scanning.

## Generated code

Protobuf and feature-flag bindings are checked in. Their generation inputs and
tools are under [pb/](../../pb/), [genproto/](genproto/) and [flags.json](flags.json),
with generation directives in [main.go](main.go). Regeneration is needed when
those interfaces change, rather than for a normal service build.
