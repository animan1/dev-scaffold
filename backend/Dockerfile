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
# For dev, allow lock to drift; for prod we'll use --frozen in the image build stage
RUN uv sync --all-extras

# ---- runtime image ----
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY --from=base /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

# App source
COPY src ./src
COPY pyproject.toml uv.lock ./

# Default command is the dev server (overridden in compose as needed)
CMD ["python", "-m", "app.manage", "runserver", "0.0.0.0:8000"]
