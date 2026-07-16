# Distributed Route Risk Engine

The Distributed Route Risk Engine is a senior project that compares driving routes using weather, roadway construction, restrictions, closures, and time-of-day conditions.

It is built as a distributed system using FastAPI, Redis, Celery workers, and Docker Compose.

---

## What It Does

A user enters origin and destination coordinates through a browser dashboard.

The system then:

1. Generates up to three driving-route alternatives.
2. Removes alternatives that are too similar.
3. Divides each route into geographic checkpoints.
4. Loads relevant weather and roadway-event data.
5. Sends checkpoint analysis tasks through Redis.
6. Processes checkpoints across multiple Celery workers.
7. Scores each checkpoint for risk.
8. Combines the checkpoint results into route-level summaries.
9. Identifies blocked routes.
10. Recommends the best available route.

The system currently evaluates factors including:

- Weather conditions
- Wind
- Visibility
- Construction
- Road restrictions
- Wet roads
- Snow and ice
- Full road closures
- Daytime or nighttime travel

A verified full closure is treated as a blocking condition. It cannot be hidden by averaging it together with safer checkpoints.

### Main Features

- Interactive browser dashboard
- Up to three route alternatives
- Full route geometry on a Leaflet map
- Optional checkpoint markers
- Live Open-Meteo weather
- Arizona 511 roadway events
- Nevada 511 roadway events
- Utah UDOT roadway events
- Automatic state detection from route geometry
- Day and Night analysis
- Construction and closure matching
- Distributed progress tracking
- Route-level scoring and recommendation
- Persistent Previous Analyses history
- Run-again-with-current-conditions option
- Standalone HTML report export
- API health and capability reporting
- Automated final validation
- Worker-scaling benchmarks and graphs

### How the Main Components Work Together

```text
Browser Dashboard
        |
        v
FastAPI Backend
        |
        v
Routing Provider
        |
        v
Routes and Checkpoints
        |
        v
Redis Task Queue
        |
        v
Celery Worker Containers
        |
        v
Weather and Roadway Analysis
        |
        v
Checkpoint Scores
        |
        v
Route Summaries and Recommendation
```

FastAPI coordinates the complete request.

Redis holds the queued checkpoint tasks.

Celery workers process those tasks in parallel.

The route-risk scoring and aggregation modules convert the completed checkpoint results into an explainable recommendation.

---

## How to Set It Up

### Requirements

The verified Windows setup uses:

- Windows 10 or Windows 11
- Python 3
- Docker Desktop
- PowerShell
- Git
- A modern web browser
- Internet access
- An OpenRouteService API key
- Roadway-provider keys for the state feeds being used

Docker Desktop must be running before starting the complete application.

### 1. Create the Python Environment

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Create the External API-Key Folder

API keys should not be placed inside the repository.

The simplest recommended location is:

```text
C:\Users\<your-windows-username>\.route-risk-keys
```

For example:

```text
C:\Users\nates\.route-risk-keys
```

Create the folder if needed:

```powershell
New-Item -ItemType Directory -Force "$HOME\.route-risk-keys"
```

### 3. Add the Key Files

Place the following files inside the external key folder:

```text
.route-risk-keys/
|-- ORSKey.txt
|-- Arizona511Key.txt
|-- Nevada511Key.txt
`-- UtahUDOTKey.txt
```

Only include files for providers you intend to use.

Each file may contain only the API key:

```text
your-api-key-here
```

It may also contain a recognized label followed by the key:

```text
ORS Key
your-api-key-here
```

Example Nevada file:

```text
Nevada 511 Key
your-api-key-here
```

The application checks an environment variable first. If that variable is empty, it reads the corresponding external key file.

The supported environment-variable names are:

```text
ORS_API_KEY
ARIZONA_511_API_KEY
NEVADA_511_API_KEY
UTAH_UDOT_API_KEY
```

The key filenames are defined in:

```text
route_risk/config.py
```

The Redis connection is separately configured in:

```text
app/core/config.py
```

### Key-Folder Locations Checked Automatically

The Windows launcher checks these locations in order:

```text
A folder passed with -KeyDirectory
ROUTE_RISK_KEYS_HOST_DIR
$HOME\OneDrive\Desktop\ORS Key
$HOME\Desktop\ORS Key
$HOME\.route-risk-keys
```

Using `$HOME\.route-risk-keys` is the simplest portable option.

### Use a Different Key Folder

A custom folder may be supplied directly:

```powershell
.\scripts\open_route_dashboard.ps1 `
    -KeyDirectory "C:\path\to\your\key-folder"
```

It may also be supplied through an environment variable:

```powershell
$env:ROUTE_RISK_KEYS_HOST_DIR = "C:\path\to\your\key-folder"
.\scripts\open_route_dashboard.ps1
```

The launcher mounts that Windows folder read-only inside the Docker containers at:

```text
/run/secrets/route-risk-keys
```

Inside Docker, Python receives:

```text
ROUTE_RISK_KEY_DIRECTORY=/run/secrets/route-risk-keys
```

### 4. Start the Application

Activate the virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

Make sure Docker Desktop is running.

Start the complete system:

```powershell
.\scripts\open_route_dashboard.ps1
```

By default, the launcher starts:

- Redis
- The FastAPI backend
- Eight Celery worker containers
- The browser dashboard server

It then opens the dashboard automatically.

Dashboard:

```text
http://127.0.0.1:8080
```

Backend API:

```text
http://127.0.0.1:8000
```

API documentation:

```text
http://127.0.0.1:8000/docs
```

### 5. Confirm the System Is Healthy

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health |
    ConvertTo-Json -Depth 8
```

A successful response should show:

- Overall status is healthy
- Redis is connected
- Celery workers are ready
- Route jobs are ready
- Route comparison is ready
- Routing and weather providers are available
- Supported roadway states include Arizona, Nevada, and Utah

### 6. Stop the Docker Services

```powershell
docker compose down
```

The dashboard server runs as a separate local Python process and may remain active after the Docker containers stop.

---

## Using the Dashboard

1. Open `http://127.0.0.1:8080`.
2. Enter origin latitude and longitude.
3. Enter destination latitude and longitude.
4. Select Day or Night.
5. Run the analysis.
6. Wait for the distributed checkpoint tasks to complete.
7. Compare the route cards.
8. Select routes to view them on the map.
9. Show checkpoint markers when needed.
10. Review construction, closure, weather, and scoring details.
11. Review the final recommendation.
12. Export an HTML report if needed.

Completed analyses appear under **Previous Analyses**.

The dashboard retains the newest ten completed analyses and can reopen them after Docker or computer restarts.

---

## Why It Was Created

This project originally began as a general Distributed AI Task Orchestrator.

The original system used artificial matrix and vector workloads to demonstrate:

- FastAPI job submission
- Redis task queuing
- Celery worker processing
- Job progress tracking
- Docker worker scaling
- Runtime and throughput benchmarking

The architecture worked, but the artificial workload did not solve a practical problem.

The project was therefore redirected toward route-risk analysis while preserving the original distributed architecture:

```text
FastAPI
   ->
Redis
   ->
Celery Workers
   ->
Results and Status Tracking
   ->
Benchmarking
```

The main change was the workload.

Instead of distributing artificial calculations, the system now distributes independent route-checkpoint risk analyses.

This made the final project more:

- Practical
- Demonstrable
- Resume-worthy
- Relevant to backend development
- Relevant to distributed systems
- Useful for showing external API integration
- Appropriate for performance and scaling analysis

The project is not intended to replace a commercial navigation application. Its purpose is to demonstrate how distributed task processing can be applied to a practical transportation-related problem.

---

## Project Structure

```text
distributed-ai-task-orchestrator/
|
|-- app/
|   |-- api/           FastAPI endpoints and job coordination
|   |-- core/          Redis configuration
|   |-- dashboard/     Browser dashboard and dashboard server
|   `-- worker/        Celery application and worker tasks
|
|-- route_risk/
|   |-- core/          Checkpoint scoring and route aggregation
|   |-- integrations/  Routing, weather, and roadway providers
|   |-- testing/       Route-risk testing helpers
|   `-- config.py      External API-key loading
|
|-- scripts/
|   |-- open_route_dashboard.ps1
|   |-- validate_final_project.py
|   |-- plot_benchmarks.py
|   `-- test and benchmark scripts
|
|-- benchmarks/        Results, graphs, and validation reports
|-- data/route_history Local saved dashboard analyses
|-- final_evidence/    Final reports and benchmark evidence
|-- docker-compose.yml
|-- Dockerfile
|-- requirements.txt
`-- README.md
```

### Important File Responsibilities

`app/api/`

Coordinates route requests, job status, summaries, health checks, and comparison results.

`app/worker/`

Defines Celery and the tasks processed by worker containers.

`route_risk/core/`

Contains the route-risk scoring and aggregation logic.

`route_risk/integrations/`

Connects to routing, weather, and state roadway-event providers.

`route_risk/config.py`

Loads ORS and state roadway API keys from environment variables or external files.

`scripts/open_route_dashboard.ps1`

Starts the complete Windows demonstration environment.

`scripts/validate_final_project.py`

Runs the unified final project validation.

---

## Validation and Performance

Run the final validation with the application running:

```powershell
python .\scripts\validate_final_project.py
```

The final release-candidate validation completed with:

```text
PASS: 13
FAIL: 0
SKIP: 0
TOTAL: 13
OVERALL STATUS: PASS
```

The validation covers:

- Required files
- Python syntax
- Provider registration
- Automatic state detection
- Day and Night schedule behavior
- Geometry-aware closure matching
- Construction and closure logic
- Duplicate-route filtering
- API health
- Worker processing
- Route comparison
- Blocked-route handling
- Long-route error handling
- Benchmark evidence

### Final Scaling Result

The final benchmark used 40 route-risk checkpoint tasks.

Increasing from one worker to eight workers produced approximately:

```text
7.34x runtime improvement
7.34x throughput improvement
4.375-second eight-worker runtime
```

This exceeded the senior-project requirement of demonstrating at least two-times improvement.

Benchmark graphs can be generated with:

```powershell
python .\scripts\plot_benchmarks.py
```

---

## Current Limitations

- The dashboard accepts coordinates rather than typed street addresses.
- OpenRouteService alternative-route requests are limited to approximately 100 kilometers or 62 miles.
- Roadway-event coverage is currently limited to Arizona, Nevada, and Utah.
- External providers may be unavailable or may change their data.
- Scoring weights are not user configurable.
- Driver profiles are not implemented.
- A self-hosted routing server is not included.
- The application is a prototype and does not guarantee road safety.

---

## Author

Nathan Shumway

CSE 499 Senior Project  
Brigham Young University-Idaho