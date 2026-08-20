FROM nginx:1.27.5-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10

RUN rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /tmp/nginx/client-body /tmp/nginx/fastcgi /tmp/nginx/proxy \
        /tmp/nginx/scgi /tmp/nginx/uwsgi \
    && chown -R nginx:nginx /tmp/nginx

COPY profiles/server-rendered-django/release-nginx.conf /etc/nginx/nginx.conf

USER nginx

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
