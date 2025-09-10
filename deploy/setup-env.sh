#!/usr/bin/env bash
# setup-env.sh: generate deploy/.env.prod with defaults for local/prod use
# Usage: ./deploy/setup-env.sh
#
# - Generates a strong DJANGO_SECRET_KEY and POSTGRES_PASSWORD by default.
# - Honors existing environment variables if you export them before running:
#     DJANGO_SECRET_KEY=... POSTGRES_PASSWORD=... ./deploy/setup-env.sh
# - Will not overwrite an existing deploy/.env.prod.

set -euo pipefail

ENV_FILE="deploy/.env.prod"

mkdir -p deploy

if [ -f "$ENV_FILE" ]; then
  echo "⚠️  $ENV_FILE already exists. Refusing to overwrite."
  echo "Remove or rename the file if you want to regenerate it."
  exit 1
fi

# Generate a random Django secret key unless provided
if [ -n "${DJANGO_SECRET_KEY:-}" ]; then
  SECRET_KEY="$DJANGO_SECRET_KEY"
else
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
fi

# Generate a strong Postgres password unless provided
if [ -n "${POSTGRES_PASSWORD:-}" ]; then
  PG_PASSWORD="$POSTGRES_PASSWORD"
else
  PG_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi

# Sensible defaults (override via env if desired)
POSTGRES_USER="${POSTGRES_USER:-app}"
POSTGRES_DB="${POSTGRES_DB:-app}"
DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1}"
REDIS_URL_DEFAULT="${REDIS_URL:-redis://redis:6379/0}"

cat > "$ENV_FILE" <<EOF
# Django settings
DJANGO_SECRET_KEY=${SECRET_KEY}
DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}

# Database (Postgres)
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${PG_PASSWORD}
POSTGRES_DB=${POSTGRES_DB}
DATABASE_URL=postgresql://\${POSTGRES_USER}:\${POSTGRES_PASSWORD}@db:5432/\${POSTGRES_DB}

# Redis (if enabled)
REDIS_URL=${REDIS_URL_DEFAULT}
EOF

echo "✅ Created $ENV_FILE"
echo "   • DJANGO_SECRET_KEY set (hidden)"
echo "   • POSTGRES_PASSWORD set (hidden)"
echo "   • Edit $ENV_FILE to adjust values. Keep it out of version control."
