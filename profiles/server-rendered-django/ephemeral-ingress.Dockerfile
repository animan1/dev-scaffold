FROM nginx:1.27.5-alpine
ARG EPHEMERAL_HOST=127.0.0.1

RUN apk add --no-cache openssl \
    && mkdir -p /etc/nginx/ephemeral-certs \
    && openssl req -x509 -nodes -newkey rsa:2048 -days 7 \
      -keyout /etc/nginx/ephemeral-certs/server.key \
      -out /etc/nginx/ephemeral-certs/server.crt \
      -subj "/CN=${EPHEMERAL_HOST}" \
      -addext "subjectAltName=DNS:${EPHEMERAL_HOST},IP:127.0.0.1,DNS:localhost"

COPY profiles/server-rendered-django/ephemeral-ingress.conf.template \
    /etc/nginx/templates/default.conf.template
