ARG NODE_IMAGE=node:20
FROM ${NODE_IMAGE}
ENV COREPACK_HOME=/opt/corepack
RUN corepack enable
WORKDIR /opt/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
WORKDIR /workspace/frontend
