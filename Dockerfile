# Venus OS Governance - Dockerfile

FROM python:3.12-slim@sha256:4b7e9f1f8b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    dbus \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv pip install --system -e .

# Copy source code
COPY src/ ./src/

# Create config directory
RUN mkdir -p /app/config/policies

# Copy example policy config
COPY config/policies.yaml.example /app/config/policies/default-safety.yaml

# Expose metrics port (if Prometheus metrics are added)
# EXPOSE 9090

# Environment variables
ENV PYTHONUNBUFFERED=1
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