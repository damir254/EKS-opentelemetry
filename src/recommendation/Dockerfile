# syntax=docker/dockerfile:1.8


FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS base

# DLA-4772-1 fixes PCRE2 vulnerabilities in the pinned upstream image.
RUN apt-get update && \
    apt-get install --yes --no-install-recommends --only-upgrade \
      libpcre2-8-0=10.42-1+deb12u1 && \
    rm -rf /var/lib/apt/lists/*

FROM base AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app


RUN /usr/local/bin/python -m venv --without-pip "${VIRTUAL_ENV}"

COPY ./src/recommendation/requirements.txt ./


RUN --mount=type=cache,target=/root/.cache/pip \
    /usr/local/bin/python -m pip --python "${VIRTUAL_ENV}" install \
      --no-cache-dir \
      --disable-pip-version-check \
      --require-hashes \
      -r requirements.txt


FROM base AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app


# Package installers are build tools. Keep pip's vendored dependencies and
# ensurepip's bundled wheel out of the final application filesystem.
RUN /usr/local/bin/python -m pip uninstall --yes pip && \
    rm -rf /usr/local/lib/python3.13/ensurepip && \
    groupadd --system app && \
    useradd --system --gid app --create-home app

COPY --from=deps /opt/venv /opt/venv


COPY ./src/recommendation/recommendation_server.py ./src/recommendation/metrics.py ./src/recommendation/logger.py ./
COPY ./src/recommendation/demo_pb2.py ./src/recommendation/demo_pb2_grpc.py ./


USER app

ENTRYPOINT ["python", "recommendation_server.py"]
