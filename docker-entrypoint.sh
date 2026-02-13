#!/bin/bash
set -e

CONFIG_DIR="/root/.nanobot"
mkdir -p "$CONFIG_DIR" /data/workspace/memory

# If cloud config exists, copy and substitute env vars
if [ -f /app/config.cloud.json ]; then
    cp /app/config.cloud.json "$CONFIG_DIR/config.json"

    # Substitute all ${VAR_NAME} patterns with actual env values
    for var in $(env | cut -d= -f1); do
        value=$(printenv "$var" | sed 's/[&/\]/\\&/g')
        sed -i "s|\${${var}}|${value}|g" "$CONFIG_DIR/config.json" 2>/dev/null || true
    done

    echo "✓ Config generated from cloud template"
fi

# Run nanobot with whatever command was passed
exec nanobot "$@"
