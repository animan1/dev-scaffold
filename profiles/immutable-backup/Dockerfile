ARG POSTGRES_IMAGE=postgres:16.10-bookworm@sha256:38471f330eb885e04de130b768d6db4e10469e2311879c7e5c699f6d2d8a1c74
ARG RESTIC_IMAGE=restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510
ARG RCLONE_IMAGE=rclone/rclone:1.74.4@sha256:c61954aaa32328a5486715dd063a81c7879f5195ad3505cd362deddd509dc4a1

FROM ${RESTIC_IMAGE} AS restic
FROM ${RCLONE_IMAGE} AS rclone
FROM ${POSTGRES_IMAGE}

RUN groupadd --gid 10001 scaffold-backup \
    && useradd --uid 10001 --gid scaffold-backup --create-home scaffold-backup \
    && mkdir -p /backup-status /workspace \
    && chown -R scaffold-backup:scaffold-backup /backup-status /workspace

COPY --from=restic /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=restic /usr/bin/restic /usr/local/bin/restic
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone

USER scaffold-backup
WORKDIR /workspace
ENTRYPOINT []
CMD ["sh"]
