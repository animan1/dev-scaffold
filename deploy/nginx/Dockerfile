# ---------- Frontend build (prod) ----------
FROM node:20 AS frontend
WORKDIR /fe
RUN corepack enable

# Copy manifests first for better layer caching
COPY frontend/package.json ./package.json
COPY frontend/pnpm-lock.yaml ./pnpm-lock.yaml

# Install deps (use cached pnpm store if available)
RUN --mount=type=cache,target=/root/.pnpm-store \
    pnpm install --frozen-lockfile || pnpm install

# Copy source and build
COPY frontend/ ./
RUN pnpm build   # outputs to /fe/dist

# ---------- Nginx runtime ----------
FROM nginx:1.27 AS runtime
RUN apt-get install openssl ca-certificates curl

# Nginx config (TLS; HTTP->HTTPS redirect handled in default.prod.conf)
COPY deploy/nginx/default.prod.conf /etc/nginx/conf.d/default.conf

# Copy built frontend into web root
COPY --from=frontend /fe/dist /usr/share/nginx/html
