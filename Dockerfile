# Venus OS Governance - Dockerfile

# Use digest only (pinned base image for reproducibility)
FROM python@sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64

WORKDIR /app

# Install system dependencies and uv (pinned version) in single layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    dbus \
    && pip install --no-cache-dir --only-binary :all: "uv==0.7.13" \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files explicitly (no glob)
COPY pyproject.toml uv.lock README.md ./

# Install dependencies from uv.lock (project not installed yet - src not copied)
RUN uv sync --frozen --no-install-project

# Copy source code and install the project (editable)
COPY src/ ./src/
RUN uv sync --frozen

# Create config directory and copy example policy
COPY config/ ./config/
RUN mkdir -p /app/config/policies \
    && cp config/policies.yaml.example /app/config/policies/default-safety.yaml

# Expose metrics port (if Prometheus metrics are added)
# EXPOSE 9090

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"
ENV DBUS_SYSTEM_BUS_ADDRESS=unix:path=/host/var/run/dbus/system_bus_socket
ENV POLICY_DIR=/app/config/policies
ENV EVENT_DB_PATH=/var/lib/dbus-event-log/events.db

# Run as non-root user
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import venus_os_governance; print('OK')" || exit 1

CMD ["python", "-m", "venus_os_governance.cli", "monitor", "--interval", "5.0"]
