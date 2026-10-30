FROM alpine:3.21.3

RUN apk add --no-cache openssh-client postgresql16-client

COPY --chmod=0755 profiles/remote-production-db/tunnel.sh /usr/local/bin/scaffold-db-tunnel

ENTRYPOINT ["/usr/local/bin/scaffold-db-tunnel"]
