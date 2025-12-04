# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

# System deps (build + runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user
ARG APP_USER=app
RUN useradd -m -u 10001 ${APP_USER}

WORKDIR /app

# Leverage Docker layer caching: copy requirements first
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy source
COPY . /app

# Minimal healthcheck: fails fast if python cannot import entrypoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('agent') else 1)"

# Run as non-root
USER ${APP_USER}

# Default: dry-run safe command; override in compose or kubernetes args
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Mountable cache dir (also declared as a volume in compose)
ENV AGENT_CACHE_DIR=/app/.agent_cache

# Optional: default args; override with CMD/compose
CMD ["python", "main.py", "--dry-run", "--env", "dev", "--service", "dehnlicense", "--hours", "24", "--limit", "50"]