# ---------- Frontend build (prod) ----------
ARG NODE_IMAGE=node:20
ARG NGINX_IMAGE=nginx:1.27
FROM ${NODE_IMAGE} AS frontend
WORKDIR /fe
RUN corepack enable

# Copy manifests first for better layer caching
COPY frontend/package.json ./package.json
COPY frontend/pnpm-lock.yaml ./pnpm-lock.yaml

# Install deps (use cached pnpm store if available)
RUN --mount=type=cache,target=/root/.pnpm-store \
    pnpm install --frozen-lockfile

# Copy source and build
COPY frontend/ ./
RUN pnpm build   # outputs to /fe/dist

# ---------- Nginx runtime ----------
FROM ${NGINX_IMAGE} AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends openssl ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Nginx config (TLS; HTTP->HTTPS redirect handled in default.prod.conf)
COPY deploy/nginx/default.prod.conf /etc/nginx/conf.d/default.conf

# Copy built frontend into web root
COPY --from=frontend /fe/dist /usr/share/nginx/html
