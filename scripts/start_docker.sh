#!/bin/bash

set -e

WORKER_COUNT="$1"

if [ -z "$WORKER_COUNT" ]; then
    echo "How many Celery worker processes/concurrency should Docker use?"
    echo "Examples: 1, 2, 4, 8, 16"
    read -r WORKER_COUNT
fi

if ! [[ "$WORKER_COUNT" =~ ^[0-9]+$ ]]; then
    echo "Error: Worker count must be a positive whole number."
    exit 1
fi

if [ "$WORKER_COUNT" -lt 1 ]; then
    echo "Error: Worker count must be at least 1."
    exit 1
fi

echo "Starting Docker environment with worker concurrency: $WORKER_COUNT"

export WORKER_CONCURRENCY="$WORKER_COUNT"

docker compose up --build