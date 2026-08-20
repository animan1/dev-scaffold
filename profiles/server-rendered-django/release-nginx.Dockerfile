FROM nginx:1.27.5-alpine

RUN rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /tmp/nginx/client-body /tmp/nginx/fastcgi /tmp/nginx/proxy \
        /tmp/nginx/scgi /tmp/nginx/uwsgi \
    && chown -R nginx:nginx /tmp/nginx

COPY profiles/server-rendered-django/release-nginx.conf /etc/nginx/nginx.conf

USER nginx

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
