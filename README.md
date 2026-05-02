# Distributed AI Task Orchestrator

## Overview
This project is a distributed task execution system built using FastAPI, Celery, and Redis. It allows users to submit batches of computational tasks, process them asynchronously across multiple workers, and track progress and performance in real time.

The system is designed to simulate AI workload orchestration and analyze distributed system performance.

---

## Core Features

- Batch task submission via API
- Asynchronous task execution using Celery
- Distributed worker processing
- Real-time job status tracking
- Retry handling with failure simulation
- Benchmarking system for performance analysis
- Multi-worker scalability testing
- Centralized development startup script

---

## Tech Stack

- Python
- FastAPI
- Celery
- Redis
- Docker (planned)
- Bash scripting

---

## Project Structure


distributed-ai-task-orchestrator/
│
├── app/
│ ├── main.py
│ ├── worker/
│ │ ├── celery_app.py
│ │ └── tasks.py
│ └── core/
│ └── models.py
│
├── scripts/
│ ├── benchmark.py
│ └── start_dev.sh
│
├── benchmarks/
│ └── results.csv
│
└── README.md


---

## Running the System (DEV MODE)

### 1. Activate virtual environment

source .venv/bin/activate


### 2. Start everything (API + Workers)

./scripts/start_dev.sh 4


Replace `4` with desired worker concurrency (1, 2, 4, 8, etc.)

---

## API Endpoints

### Submit Batch

POST /submit_batch


Example:

curl -X POST "http://127.0.0.1:8000/submit_batch
"
-H "Content-Type: application/json"
-d '{"numbers":[1,2,3,4]}'


---

### Submit Slow Batch (for benchmarking)

POST /submit_slow_batch


---

### Submit Unreliable Batch (for retry testing)

POST /submit_unreliable_batch


---

### Check Job Status

GET /job_status/{job_id}


---

### Get Results

GET /results/{job_id}


---

## Benchmarking

Run performance tests using:


python3 scripts/benchmark.py --tasks 100 --delay 1 --workers 4


### Parameters:
- `--tasks`: number of tasks
- `--delay`: seconds per task (simulates workload)
- `--workers`: number of workers used

---

## Benchmark Output

Results are saved to:

benchmarks/results.csv


Example output:

timestamp,task_count,delay_seconds,total_runtime_seconds,throughput_tasks_per_second,final_status,completed_tasks,failed_tasks


---

## Automated Scaling Experiment

The project includes a scaling experiment script that automatically tests multiple worker concurrency levels.

Example:

```bash
./scripts/run_scaling_experiment.sh 1 2 4


## What Has Been Implemented

- Distributed task execution
- Worker scaling (1–8 workers tested)
- Retry logic with failure tracking
- Real-time progress tracking
- Benchmarking + CSV logging
- Centralized dev startup script

---

## Next Steps

- Visualization of benchmark results (graphs)
- Docker Compose setup for full environment
- Load testing at higher scale (500+ tasks)
- Queue prioritization / routing
- Persistent job storage (DB)

---

## Purpose

This project demonstrates:
- Distributed systems design
- Parallel processing
- Fault tolerance and retries
- Performance scaling analysis
- AI-style workload orchestration

---

## Author
Nathan Shumway