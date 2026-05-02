#!/bin/bash

set -e

TASK_COUNT=20
DELAY_SECONDS=1

if [ "$#" -lt 1 ]; then
    echo "Usage: ./scripts/run_scaling_experiment.sh <worker_count_1> <worker_count_2> ..."
    echo "Example: ./scripts/run_scaling_experiment.sh 1 2 4"
    exit 1
fi

for WORKER_COUNT in "$@"; do
    if ! [[ "$WORKER_COUNT" =~ ^[0-9]+$ ]]; then
        echo "Error: Worker count must be a positive whole number. Invalid value: $WORKER_COUNT"
        exit 1
    fi

    if [ "$WORKER_COUNT" -lt 1 ]; then
        echo "Error: Worker count must be at least 1. Invalid value: $WORKER_COUNT"
        exit 1
    fi
done

wait_for_api() {
    echo "Waiting for API to become ready..."

    for i in {1..30}; do
        if curl -s "http://127.0.0.1:8000/docs" > /dev/null; then
            echo "API is ready."
            return 0
        fi

        echo "API not ready yet... attempt $i/30"
        sleep 1
    done

    echo "Error: API did not become ready in time."
    return 1
}

cleanup_docker() {
    echo "Stopping Docker environment..."
    docker compose down
}

echo "Starting scaling experiment"
echo "Task count: $TASK_COUNT"
echo "Delay seconds per task: $DELAY_SECONDS"
echo "Worker counts: $*"

for WORKER_COUNT in "$@"; do
    echo ""
    echo "========================================"
    echo "Running benchmark with $WORKER_COUNT worker(s)"
    echo "========================================"

    cleanup_docker

    export WORKER_CONCURRENCY="$WORKER_COUNT"

    docker compose up --build -d

    wait_for_api

    python3 scripts/benchmark.py \
        --tasks "$TASK_COUNT" \
        --delay "$DELAY_SECONDS" \
        --workers "$WORKER_COUNT"

    cleanup_docker

    echo "Completed benchmark with $WORKER_COUNT worker(s)"
done

echo ""
echo "Generating benchmark graphs..."
python3 scripts/plot_benchmarks.py

echo ""
echo "Scaling experiment complete."
echo "Results saved to benchmarks/results.csv"
echo "Graphs saved to benchmarks/graphs/"