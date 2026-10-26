#!/bin/sh
set -eu
umask 077
config="${TOKENAGE_CONFIG:-${TOKENAGE_HOME:-/data}/config.yaml}"
if [ ! -f "$config" ]; then
    cp /app/config.example.yaml "$config"
fi
if [ ! -r "$config" ]; then
    echo "Cannot read $config: grant container UID 10001 read access to the mounted config." >&2
    exit 1
fi
python -m src.cli migrate
exec "$@"
