#!/bin/sh
set -eu

require_value() {
    name="$1"
    eval "value=\${$name:-}"
    if [ -z "$value" ]; then
        echo "Missing required $name." >&2
        exit 2
    fi
}

require_safe_name() {
    name="$1"
    eval "value=\${$name}"
    case "$value" in
        *[!A-Za-z0-9._-]* | -* | "")
            echo "$name contains unsupported characters." >&2
            exit 2
            ;;
    esac
}

require_port() {
    name="$1"
    eval "value=\${$name}"
    case "$value" in
        *[!0-9]* | "")
            echo "$name must be a TCP port." >&2
            exit 2
            ;;
    esac
    if [ "$value" -lt 1 ] || [ "$value" -gt 65535 ]; then
        echo "$name must be between 1 and 65535." >&2
        exit 2
    fi
}

for name in SSH_HOST SSH_USER SSH_PORT REMOTE_DB_HOST REMOTE_DB_PORT TUNNEL_PORT; do
    require_value "$name"
done
require_safe_name SSH_HOST
require_safe_name SSH_USER
require_safe_name REMOTE_DB_HOST
require_port SSH_PORT
require_port REMOTE_DB_PORT
require_port TUNNEL_PORT

identity=/run/secrets/ssh_identity
known_hosts=/run/secrets/ssh_known_hosts
if [ ! -r "$identity" ]; then
    echo "SSH identity is not readable at $identity." >&2
    exit 2
fi
if [ ! -r "$known_hosts" ]; then
    echo "SSH known_hosts is not readable at $known_hosts." >&2
    exit 2
fi

exec ssh -NT \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o IdentitiesOnly=yes \
    -o ServerAliveInterval="${SSH_SERVER_ALIVE_INTERVAL:-15}" \
    -o ServerAliveCountMax="${SSH_SERVER_ALIVE_COUNT_MAX:-3}" \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$known_hosts" \
    -i "$identity" \
    -p "$SSH_PORT" \
    -L "0.0.0.0:${TUNNEL_PORT}:${REMOTE_DB_HOST}:${REMOTE_DB_PORT}" \
    "${SSH_USER}@${SSH_HOST}"
