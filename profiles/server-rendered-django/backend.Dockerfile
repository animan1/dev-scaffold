FROM ghcr.io/astral-sh/uv:0.8.17 AS uv

FROM python:3.12.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/workspace/backend/src

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /workspace/profiles/server-rendered-django
COPY profiles/server-rendered-django/pyproject.toml profiles/server-rendered-django/uv.lock ./

FROM base AS development

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git make \
    && rm -rf /var/lib/apt/lists/* \
    && uv sync --frozen --all-extras \
    && chown -R app:app /opt/venv /home/app \
    && git config --system --add safe.directory /workspace

USER app
WORKDIR /workspace/backend
COPY --chown=app:app backend/ .

CMD ["uv", "run", "python", "-m", "app.manage", "runserver", "0.0.0.0:8000"]

FROM base AS production

ENV DJANGO_SETTINGS_MODULE=app.project.settings.prod \
    DATABASE_URL=postgresql://app:app@db:5432/app

RUN uv sync --frozen --no-dev \
    && mkdir -p /static /workspace/backend \
    && chown -R app:app /opt/venv /workspace/backend /home/app /static

USER app
WORKDIR /workspace/backend
COPY --chown=app:app backend/src ./src
RUN DJANGO_SECRET_KEY=build-only-not-for-runtime \
    python -m app.manage collectstatic --noinput

CMD ["gunicorn", "app.project.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "60"]
