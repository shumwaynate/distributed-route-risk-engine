# PROJECT HANDOFF — Distributed Route Risk Engine

**Originally created:** 2026-06-04
**Last updated:** 2026-06-19

## Project folder

`distributed-ai-task-orchestrator`

## Project purpose

This senior project began as a Distributed AI Task Orchestrator using FastAPI, Redis, Celery, Docker Compose, and configurable worker scaling.

The project has since evolved into a Distributed Route Risk Engine. The original distributed architecture is now used to process real route-analysis workloads instead of artificial matrix, vector, square-number, or delayed benchmark tasks.

The system can generate driving-route alternatives, sample route checkpoints, fetch live weather, load roadway events, distribute checkpoint analysis across Celery workers, calculate route-risk summaries, and compare route alternatives.

## Current high-level flow

1. A user submits origin and destination coordinates.
2. FastAPI requests one or more routes from the configured routing provider.
3. Route geometry is sampled into checkpoints.
4. Live state roadway events and optional manually supplied events are loaded.
5. Road events are matched to route checkpoints.
6. Each checkpoint becomes an independent Celery task.
7. Celery workers fetch live weather from Open-Meteo.
8. Each checkpoint is scored using weather, road conditions, road events, and nighttime information.
9. Redis stores job metadata and Celery task identifiers.
10. Completed checkpoint results are aggregated into route-level summaries.
11. Multi-route jobs compare the alternatives and select a recommended route.
12. Road closures can mark a route as blocked instead of allowing the closure to be averaged down.

## Current API endpoints

* `GET /`
* `POST /submit_routed_route_risk_job`
* `POST /submit_route_comparison_job`
* `GET /job_status/{job_id}`
* `GET /results/{job_id}`
* `GET /route_risk_summary/{job_id}`
* `GET /route_comparison_summary/{job_id}`

The old `POST /submit_route_risk_job` endpoint has been removed.

## Current API organization

### `app/api/main.py`

The FastAPI entry point is intentionally small.

It:

* creates the FastAPI application;
* registers the route and job routers;
* exposes the root health endpoint.

Business logic is no longer concentrated inside this file.

### `app/api/models.py`

Contains current Pydantic request models, including:

* `RoadEventRequest`
* `RoutedRouteRiskJobRequest`
* `RouteComparisonJobRequest`

### `app/api/job_store.py`

Contains Redis-backed job storage and status helpers, including:

* creating a job record;
* loading a job record;
* retrieving Celery task results;
* building the public job-status response.

### `app/api/routers/routes.py`

Contains route submission and summary endpoints:

* single routed route-risk submission;
* multi-route comparison submission;
* single-route summary;
* route-comparison summary.

### `app/api/routers/jobs.py`

Contains general job endpoints:

* job status;
* raw task results.

### `app/api/services/route_jobs.py`

Contains route-job orchestration logic:

* building route segments from checkpoints;
* loading live state roadway events;
* separating active and upcoming events;
* submitting single-route distributed jobs;
* submitting multi-route comparison jobs.

### `app/api/services/route_summaries.py`

Contains summary and recommendation logic:

* building single-route summaries;
* building comparison summaries;
* combining upcoming-event disclosures;
* choosing the recommended route.

## Celery worker tasks

### `app/worker/tasks.py`

The worker file now contains only Route Risk Engine tasks:

* `route_segment_risk_task`
* `live_weather_route_segment_risk_task`
* `route_risk_summary_task`

The following legacy tasks were removed:

* `square_number`
* `slow_square_number`
* `unreliable_square`
* `transient_unreliable_square`
* `matrix_compute_task`
* `vector_similarity_task`

NumPy is no longer required by the worker task file.

## Route Risk Engine domain logic

### `route_risk/core/scoring.py`

Contains local route-segment scoring rules.

It evaluates factors such as:

* temperature;
* wind;
* visibility;
* weather condition;
* road condition;
* nighttime travel;
* construction;
* snow or ice;
* road closure.

### `route_risk/core/aggregation.py`

Aggregates completed checkpoint results.

It:

* calculates the average segment score;
* identifies the highest-risk segment;
* detects blocking segments;
* marks routes as blocked when an applicable closure is present;
* preserves the average score for explanation even when the final route status is blocked.

### `route_risk/integrations/weather_client.py`

Fetches and normalizes live Open-Meteo weather.

Normalized weather information includes:

* temperature in Fahrenheit;
* wind speed in miles per hour;
* weather condition;
* visibility;
* source;
* raw weather code.

### Routing integrations

The project uses a configurable routing provider to generate driving routes and route alternatives.

Routing results include:

* distance;
* duration;
* route geometry;
* sampled checkpoints;
* route identity for multi-route comparison.

### `route_risk/integrations/road_conditions_client.py`

Matches roadway events to route checkpoints and converts event types into scoring-friendly road conditions.

Current supported concepts include:

* construction;
* maintenance;
* work zones;
* restrictions;
* closures;
* wet roads;
* snow;
* ice.

### State 511 integrations

The project includes a provider-oriented state-event loading structure.

Current implementation includes Nevada 511 roadway events.

Relevant capabilities include:

* fetching Nevada 511 event data;
* normalizing event fields;
* preserving event timing and recurrence information;
* classifying events as active, upcoming, future, expired, or unknown;
* scoring active events;
* disclosing upcoming events separately.

## Current route-comparison behavior

A route-comparison request can generate multiple route alternatives.

Each alternative:

* receives a route identifier and label;
* is sampled into checkpoints;
* fans out into independent Celery tasks;
* receives a route-risk summary;
* is included in the final comparison response.

The comparison summary can select a recommended route based on route safety and blocking conditions.

## Current blocking-route behavior

If an applicable closure matches a route:

* the route may receive `route_risk_score: 100`;
* the route risk level may become `Blocked`;
* `route_blocked` becomes `true`;
* blocking segments are listed;
* the route warning explains that rerouting is required.

A closure should not be averaged down by otherwise safe route checkpoints.

## Scaling experiment system

The scaling system now benchmarks only the real Route Risk Engine workload.

It no longer submits artificial slow, matrix, or vector workloads.

### `scripts/benchmark.py`

Submits a real routed route-risk job and records:

* timestamp;
* workload;
* task count;
* worker count;
* total runtime;
* throughput;
* final status;
* completed task count;
* failed task count.

Results are appended to:

`benchmarks/results.csv`

Historical matrix, vector, and slow-workload rows remain compatible with the existing CSV.

### `scripts/run_scaling_experiment.ps1`

Windows scaling script.

It:

* accepts worker counts as positional arguments;
* starts Redis and FastAPI through Docker Compose;
* scales Celery worker containers;
* runs one real route-risk workload for each worker count;
* records runtime and throughput;
* appends results to the benchmark CSV;
* prints a final summary table.

Example:

`$env:TASKS = "20"`
`.\scripts\run_scaling_experiment.ps1 1 2 4 8`

### `scripts/run_scaling_experiment.sh`

macOS/Linux scaling counterpart.

Example:

`TASKS=20 ./scripts/run_scaling_experiment.sh 1 2 4 8`

The Bash script has not been syntax-tested in the current Windows environment because WSL Bash is not installed.

### `scripts/plot_benchmarks.py`

Generates route-risk scaling graphs by default:

* runtime versus workers;
* throughput versus workers;
* measured speedup versus workers.

Historical workloads can still be graphed with:

`python .\scripts\plot_benchmarks.py --include-historical`

Existing historical benchmark results and graphs should be retained as evidence of iterative project development.

## Local development scripts

### `scripts/start_dev.ps1`

Starts the Windows local-development environment.

It:

* activates the virtual environment;
* checks Redis;
* starts Redis through Docker if necessary;
* starts FastAPI;
* starts a Celery worker.

Windows local development uses Celery’s solo pool for stability.

This is appropriate for functional testing but not for measuring multi-worker scaling.

### `scripts/start_dev.sh`

Provides the macOS/Linux local-development counterpart.

## Retained primary API test

### `scripts/test_routed_route_risk_api.ps1`

This is the main retained routed API test.

It:

* submits origin and destination coordinates;
* uses the current routed endpoint;
* includes predictable supplemental manual roadway events;
* polls the distributed job;
* fetches raw results;
* fetches the route-risk summary;
* prints route blocking and risk information.

The following duplicate or obsolete PowerShell tests were removed:

* `scripts/test_custom_routed_route_risk_api.ps1`
* `scripts/test_route_risk_api.ps1`
* `scripts/test_route_risk_fanout_api.ps1`

## Remaining accuracy improvements

The next technical work should focus on roadway-event accuracy rather than adding more legacy infrastructure.

### 1. Nightly and overnight closure windows

Some Nevada events are marked as broadly active by structured recurrence data even when their description says the closure only applies during nighttime hours.

Examples include descriptions such as:

* nightly 8 PM to 5 AM;
* nightly 8 PM to 6 AM.

The system should parse these descriptions and apply the closure only during the applicable time window.

### 2. Preserve provider event metadata

Nevada event metadata should remain available after roadway matching.

Useful information includes:

* roadway name;
* direction;
* ramp information;
* recurrence;
* start and end times;
* provider identifier;
* event geometry.

### 3. Roadway, direction, and ramp applicability

A nearby event should not automatically affect a route if it applies to:

* the opposite direction;
* a different roadway;
* a ramp the route does not use;
* a nearby parallel road.

### 4. Improve route-to-event geometry matching

The current matching process relies heavily on sampled route checkpoints.

Accuracy can be improved by comparing:

* full route geometry;
* event point geometry;
* event line or polyline geometry;
* roadway names;
* route direction.

### 5. All routes blocked behavior

When every generated route alternative is blocked, the system should not present one as a normal safe recommendation.

The comparison response should instead return either:

* no safe route recommendation; or
* a clearly labeled least-bad alternative.

## Refactor and cleanup status

Completed:

* split the large FastAPI file into routers, services, models, and job storage;
* removed the obsolete number-batch Pydantic model;
* removed generated Python cache files from Git tracking;
* removed `.DS_Store` files;
* removed temporary pasted-code and response files;
* removed legacy worker tasks;
* simplified scaling to the real route-risk workload;
* removed obsolete duplicate API test scripts;
* updated Windows and macOS/Linux startup and scaling scripts;
* completed the full project compile check;
* completed Docker Compose configuration validation;
* verified FastAPI route registration;
* completed the end-to-end routed job test;
* completed the multi-route comparison test;
* completed the route-risk worker-scaling experiment;
* generated runtime, throughput, and speedup graphs;
* completed Git whitespace, secret-file, temporary-file, obsolete-reference, and hard-coded-path reviews.

### Final route-risk scaling verification

The final benchmark used 16 real route-risk checkpoint tasks.

| Workers | Runtime (seconds) | Throughput (tasks/second) | Measured speedup |
| ---: | ---: | ---: | ---: |
| 1 | 14.67 | 1.09 | 1.00x |
| 2 | 7.91 | 2.02 | 1.85x |
| 4 | 5.33 | 3.00 | 2.75x |

The 1-to-4-worker run reduced runtime from 14.67 seconds to 5.33 seconds and achieved approximately 2.75x measured speedup. The final Route Risk Engine workload therefore satisfies the project requirement to demonstrate greater than two-times scaling.

## Important commands

### Start Windows local development

`.\scripts\start_dev.ps1`

### Run the retained routed API test

`.\scripts\test_routed_route_risk_api.ps1`

### Run Windows worker scaling

`$env:TASKS = "20"`
`.\scripts\run_scaling_experiment.ps1 1 2 4 8`

### Generate current Route Risk Engine graphs

`python .\scripts\plot_benchmarks.py`

### Include historical benchmark workloads

`python .\scripts\plot_benchmarks.py --include-historical`

## Current project status

The Route Risk Engine refactor, repository cleanup, functional verification, and scaling validation are complete.

The repository is ready for its final commit. Future development should focus on the route-event accuracy improvements documented above, including stronger geometry matching, roadway-direction handling, metadata preservation, and explicit behavior when every route is blocked.

## How to use this file later

Use this file as a project handoff and current-state reference.

Before continuing development in a new conversation, provide this document and state:

“Read `PROJECT_HANDOFF_ROUTE_RISK_ENGINE.md` first and use it as the current project source of truth.”
