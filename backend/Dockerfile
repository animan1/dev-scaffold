# ---- base image with uv ----
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
# Only copy files that influence deps first for caching
COPY pyproject.toml uv.lock ./
ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONPATH="/app/src:${PYTHONPATH}"

# ---- dev image ----
FROM base AS dev
# For dev, allow lock to drift; for prod we'll use --frozen in the image build stage
RUN uv sync --all-extras
# COPY src ./src
# Install tools needed by CI targets that invoke bash and make
RUN apt-get update && apt-get install -y --no-install-recommends bash make && rm -rf /var/lib/apt/lists/*
# Default command is the dev server (overridden in compose as needed)
CMD ["python", "-m", "app.manage", "runserver", "0.0.0.0:8000"]

# ---- prod target ----
FROM base AS prod
# reproducible install; no dev extras
RUN uv sync --frozen
COPY src ./src
# Gunicorn default for prod
CMD ["gunicorn", "app.project.wsgi:application", "-b", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "60"]
