ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.17-python3.12-bookworm-slim
ARG PYTHON_IMAGE=python:3.12.11-slim-bookworm
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE}
COPY --from=uv /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends git make jq \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH PYTHONPATH=/workspace/backend/src \
    PYTHONDONTWRITEBYTECODE=1 UV_NO_SYNC=1
COPY backend/pyproject.toml backend/uv.lock /inputs/backend/
COPY profiles/server-rendered-django/pyproject.toml profiles/server-rendered-django/uv.lock /inputs/server-rendered-django/
ARG PROFILE=react-vite
RUN if [ "$PROFILE" = react-vite ]; then project=/inputs/backend; \
    else project=/inputs/server-rendered-django; fi; \
    uv sync --project "$project" --frozen --all-extras --no-install-project
WORKDIR /workspace/backend
