#!/usr/bin/env bash
set -Eeuo pipefail

# Exercise the real image with a host owner distinct from the worker UID.
install -d -o 10002 -g 10001 -m 2770 /backups /publication
sed '/^case /,$d' /usr/local/bin/scaffold-backup > /tmp/backup-functions
runuser -u scaffold-backup -- bash <<'CHECK'
set -Eeuo pipefail
source /tmp/backup-functions
initialize_repository
printf shared | restic backup --stdin --stdin-filename access-check --host production --tag postgres
original="$(restic snapshots --json | jq -er '.[0].id')"
copy_repository
copy_repository
test "$(restic -r /publication snapshots --json | jq length)" = 1
test "$(restic cat config | jq -r .chunker_polynomial)" = \
  "$(restic -r /publication cat config | jq -r .chunker_polynomial)"
test "$(restic cat config | jq -r .id)" != "$(restic -r /publication cat config | jq -r .id)"
restic forget "${original}" --prune
test "$(restic snapshots --json | jq length)" = 0
restic -r /publication check --read-data
test "$(restic -r /publication dump latest access-check)" = shared
mkdir /tmp/new-working
RESTIC_REPOSITORY=/tmp/new-working initialize_repository
test "$(restic -r /tmp/new-working cat config | jq -r .chunker_polynomial)" = \
  "$(restic -r /publication cat config | jq -r .chunker_polynomial)"
if RESTIC_REPOSITORY=/publication initialize_repository; then exit 1; fi
CHECK

test "$(stat -c %u /backups)" = 10002
test "$(stat -c %u /publication)" = 10002
setpriv --reuid=10002 --regid=10001 --clear-groups restic --no-cache -r /publication snapshots

# Simulate byte transport excluding both kinds of runtime locks, then recover
# solely from the retrieved publication repository after source pruning.
mkdir /retrieved
tar -C /publication --exclude=locks -cf /tmp/publication.tar .
tar -C /retrieved -xf /tmp/publication.tar
test ! -e /retrieved/locks
restic -r /retrieved check --read-data
test "$(restic -r /retrieved dump latest access-check)" = shared
