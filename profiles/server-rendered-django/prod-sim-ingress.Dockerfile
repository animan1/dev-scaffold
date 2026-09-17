FROM nginx:1.27.5-alpine

RUN apk add --no-cache openssl \
    && mkdir -p /etc/nginx/prod-sim-certs \
    && openssl req -x509 -nodes -newkey rsa:2048 -days 7 \
      -keyout /etc/nginx/prod-sim-certs/server.key \
      -out /etc/nginx/prod-sim-certs/server.crt \
      -subj /CN=localhost \
      -addext subjectAltName=DNS:localhost,IP:127.0.0.1

COPY profiles/server-rendered-django/prod-sim-ingress.conf.template \
    /etc/nginx/templates/default.conf.template
