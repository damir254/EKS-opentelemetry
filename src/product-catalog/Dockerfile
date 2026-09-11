# syntax=docker/dockerfile:1.8

############################
# Build stage
############################
FROM golang:1.26.8-bookworm@sha256:9fdc884aacc3bec89b20ffc69f4bb369c78210e3e4f600387b5128b12c199f81 AS builder

WORKDIR /src

# Copy Go module files first for layer caching
COPY ./src/product-catalog/go.mod ./src/product-catalog/go.sum ./

RUN --mount=type=cache,target=/go/pkg/mod \
    go mod download

COPY ./src/product-catalog/genproto/oteldemo/ genproto/oteldemo/
COPY ./src/product-catalog/flags/ flags/
COPY ./src/product-catalog/main.go main.go


# Build static binary
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 \
    GOOS=linux \
    GOARCH=amd64 \
    go build \
      -trimpath \
      -ldflags="-s -w" \
      -o product-catalog \
      .

############################
# Runtime stage
############################
FROM gcr.io/distroless/static-debian13:nonroot@sha256:1c2c046bc09ed40fad370b599a0b1ae7987f55b01e247cf27a7c27cd97e5bbc7

WORKDIR /

COPY --from=builder /src/product-catalog /product-catalog

USER nonroot:nonroot

ENTRYPOINT ["/product-catalog"]
