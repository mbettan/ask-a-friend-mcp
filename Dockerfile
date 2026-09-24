# Multi-stage production Dockerfile for Ask-a-Friend Consumer MCP
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast, deterministic dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy project specification
COPY pyproject.toml ./

# Install dependencies into isolated virtual environment
RUN uv venv /app/.venv && \
    UV_HTTP_TIMEOUT=120 /bin/uv pip install --no-cache --python /app/.venv/bin/python -r pyproject.toml

# Final minimal runtime image
FROM python:3.12-slim AS runtime

WORKDIR /app

# Create non-root system user for security
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /sbin/nologin appuser

# Copy installed virtual environment and source code (docs/ excluded — GitHub Pages only)
COPY --from=builder /app/.venv /app/.venv
COPY src/ /app/src/
COPY scripts/ /app/scripts/
COPY config/ /app/config/
COPY agents/ /app/agents/
COPY pyproject.toml README.md LICENSE /app/

# Environment variables
ENV PYTHONPATH="/app/.venv:/app/scripts:/app" \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOST=0.0.0.0 \
    HOME=/app

USER appuser

EXPOSE 8080

CMD ["python", "-m", "src.server"]

