#!/bin/sh
set -eu

usage() {
    echo "Usage: $0 <upload-host> <max-request-bytes> <output-file>" >&2
    exit 2
}

[ "$#" -eq 3 ] || usage

upload_host=$1
max_request_size=$2
output_file=$3

case "$upload_host" in
    ""|*[!A-Za-z0-9.-]*)
        echo "Invalid upload host: use only letters, digits, dots, and hyphens." >&2
        exit 2
        ;;
esac

case "$max_request_size" in
    ""|*[!0-9]*)
        echo "Invalid max request size: expected a positive integer byte count." >&2
        exit 2
        ;;
esac

if [ "$max_request_size" -le 0 ]; then
    echo "Invalid max request size: expected a positive integer byte count." >&2
    exit 2
fi

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
template="$script_dir/dynamic.yml.template"
output_dir=$(dirname "$output_file")
tmp_file="${output_file}.tmp.$$"

mkdir -p "$output_dir"
trap 'rm -f "$tmp_file"' EXIT HUP INT TERM

sed \
    -e "s|@@UPLOAD_HOST@@|$upload_host|g" \
    -e "s|@@MAX_REQUEST_SIZE@@|$max_request_size|g" \
    "$template" > "$tmp_file"

chmod 0640 "$tmp_file"
mv "$tmp_file" "$output_file"
trap - EXIT HUP INT TERM

echo "Rendered Traefik configuration to $output_file"
