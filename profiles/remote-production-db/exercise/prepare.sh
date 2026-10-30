#!/bin/sh
set -eu

root=/work
host=${EXERCISE_SSH_HOST:-host.docker.internal}
port=${EXERCISE_SSH_PORT:-22222}

rm -f \
    "$root/authorized_keys" \
    "$root/client" \
    "$root/client.pub" \
    "$root/host" \
    "$root/host.pub" \
    "$root/known_hosts"

ssh-keygen -q -t ed25519 -N '' -C exercise-client -f "$root/client"
ssh-keygen -q -t ed25519 -N '' -C exercise-host -f "$root/host"
cp "$root/client.pub" "$root/authorized_keys"
set -- $(cat "$root/host.pub")
printf '[%s]:%s %s %s\n' "$host" "$port" "$1" "$2" > "$root/known_hosts"
chmod 0600 "$root/client" "$root/host" "$root/known_hosts"
chmod 0644 "$root/authorized_keys" "$root/client.pub" "$root/host.pub"
