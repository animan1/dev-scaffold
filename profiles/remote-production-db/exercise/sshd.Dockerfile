FROM alpine:3.21.3

RUN apk add --no-cache openssh-server \
    && adduser -D tunnel \
    && passwd -d tunnel \
    && install -d -m 0700 -o tunnel -g tunnel /home/tunnel/.ssh \
    && install -d -m 0755 /run/sshd

COPY profiles/remote-production-db/exercise/sshd_config /etc/ssh/sshd_config

EXPOSE 22

CMD ["/usr/sbin/sshd", "-D", "-e", "-f", "/etc/ssh/sshd_config"]
