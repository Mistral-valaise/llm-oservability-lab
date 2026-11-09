# syntax=docker/dockerfile:1.7
# Multi-stage build: dependencies are installed in a builder, the runtime image
# contains no compiler, no pip cache and runs as a non-root user.
# Pin the base image by digest in production (Renovate keeps it fresh).
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.txt
COPY pyproject.toml .
COPY src ./src
RUN /opt/venv/bin/pip install --no-deps .

FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title="ai-gateway" \
      org.opencontainers.image.description="OpenAI-compatible LLM gateway with OpenTelemetry GenAI instrumentation" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OTEL_SEMCONV_STABILITY_OPT_IN=http
COPY --from=builder /opt/venv /opt/venv
# UID 10001 with group 0: works with a fixed UID on Kubernetes and with the
# random UID that OpenShift assigns (it always runs with GID 0).
RUN useradd --uid 10001 --gid 0 --no-create-home --shell /usr/sbin/nologin app
USER 10001
EXPOSE 8080
CMD ["uvicorn", "gateway.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8080", \
     "--no-server-header", "--no-access-log"]
