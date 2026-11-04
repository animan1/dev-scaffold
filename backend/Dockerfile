# ---- base image with uv ----
ARG PYTHON_IMAGE=python:3.12.11-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.17-python3.12-bookworm-slim
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl && rm -rf /var/lib/apt/lists/*

# Use the same committed tool input for source checks and release builds.
COPY --from=uv /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

WORKDIR /app
# Only copy files that influence deps first for caching
COPY pyproject.toml uv.lock ./
ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONPATH="/app/src"

# ---- dev image ----
FROM base AS dev
# Source preparation and production builds both honor the committed lock.
RUN uv sync --frozen --all-extras
# COPY src ./src
# Install tools needed by CI and committed-release history validation
RUN apt-get update && apt-get install -y --no-install-recommends bash git make && rm -rf /var/lib/apt/lists/*
# Default command is the dev server (overridden in compose as needed)
CMD ["python", "-m", "app.manage", "runserver", "0.0.0.0:8000"]

# ---- prod target ----
FROM base AS prod
ENV DJANGO_SETTINGS_MODULE=app.project.settings.prod
# reproducible install; no dev extras
RUN uv sync --frozen
COPY src ./src
RUN mkdir -p /static
RUN DJANGO_SECRET_KEY=build-only-not-for-runtime python -m app.manage collectstatic --noinput
# Gunicorn default for prod
CMD ["gunicorn", "app.project.wsgi:application", "-b", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "60"]
