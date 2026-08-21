#!/bin/sh
set -eu

data_dir=${1:-/srv/media-uploader}

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root (for example: sudo $0 $data_dir)." >&2
    exit 1
fi

install -d -m 0755 "$data_dir"
install -d -m 0750 "$data_dir/uploads" "$data_dir/audit" "$data_dir/redis"
install -d -m 0750 "$data_dir/letsencrypt" "$data_dir/traefik" "$data_dir/secrets"

# The application image runs as uid/gid 10001.
chown 10001:10001 "$data_dir/uploads" "$data_dir/audit"

# The production Compose file runs Traefik as uid/gid 65532.
chown 65532:65532 "$data_dir/letsencrypt"
chown root:65532 "$data_dir/traefik" "$data_dir/secrets"

printf '%s\n' \
    "Prepared $data_dir" \
    "Next: create $data_dir/secrets/upload-users.htpasswd and render $data_dir/traefik/dynamic.yml."
