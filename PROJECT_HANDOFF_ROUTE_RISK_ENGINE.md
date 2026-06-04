# PROJECT HANDOFF - Route Risk Engine

Date created: 2026-06-04

Project folder:
distributed-ai-task-orchestrator

Purpose:
This project started as a Distributed AI Task Orchestrator using FastAPI, Redis, Celery, and worker tasks. It has now pivoted into a Route Risk Engine / Driving Recommendation prototype that still uses the distributed task architecture. The current system generates routes, samples checkpoints, fetches live weather, matches road events, scores each segment, and aggregates the route into a user-facing recommendation.

Current high-level flow:
1. User submits origin and destination coordinates.
2. FastAPI calls OSRM to generate a driving route.
3. Route geometry is sampled into checkpoints.
4. Optional road events are matched to nearby checkpoints.
5. Each checkpoint becomes a Celery task.
6. Celery workers fetch live weather from Open-Meteo.
7. Each checkpoint is scored using route-risk logic.
8. Results are aggregated into a route-level summary.
9. Road closures now block the route instead of being averaged down.

Important current behavior:
- A normal route with clear weather can return Low risk.
- Construction near a checkpoint increases that checkpoint risk.
- A road closure near any checkpoint now escalates the route to:
  - route_risk_score: 100
  - route_risk_level: Blocked
  - route_blocked: true
  - route_warning explaining rerouting is needed
- The clean summary endpoint now exposes blocking route information.

Current working API endpoints:
- GET /
- POST /submit_route_risk_job
- POST /submit_routed_route_risk_job
- GET /job_status/{job_id}
- GET /results/{job_id}
- GET /route_risk_summary/{job_id}

Most important active files:

app/api/main.py
- Main FastAPI app.
- Contains original orchestrator endpoints.
- Contains direct route-risk segment endpoint.
- Contains routed route-risk endpoint.
- Accepts origin/destination coordinates.
- Calls OSRM route generation.
- Applies road-event matching.
- Submits live-weather Celery tasks.
- Builds clean route-risk summaries.
- Clean summary now includes:
  - route_blocked
  - route_warning
  - average_segment_score
  - blocking_segments
  - blocking_segment_count
  - incomplete_task_count

app/worker/tasks.py
- Celery task definitions.
- Preserves original orchestrator tasks:
  - square_number
  - slow_square_number
  - unreliable_square
  - transient_unreliable_square
  - matrix_compute_task
  - vector_similarity_task
- Route-risk tasks:
  - route_segment_risk_task
  - live_weather_route_segment_risk_task
  - route_risk_summary_task
- Live weather task fetches Open-Meteo data.
- Route-risk task output now preserves road context:
  - road_condition
  - road_condition_source
  - matched_road_event
  - nearby_road_event_count

route_risk/core/scoring.py
- Core segment and route scoring logic.
- Does not call APIs.
- Handles weather, wind, visibility, road condition, and night scoring.
- Important road conditions:
  - normal
  - construction
  - wet
  - snowy
  - icy
  - closed
- closed adds road closure factor and scores the segment as 100.

route_risk/core/aggregation.py
- Aggregates completed segment results.
- Calculates average segment score.
- Finds highest-risk segment.
- Detects blocking segments.
- If any segment has road_condition = closed or factor = road closure:
  - route_risk_score becomes 100
  - route_risk_level becomes Blocked
  - route_blocked becomes true
  - route_warning is added
  - average_segment_score is preserved for explanation

route_risk/integrations/weather_client.py
- Open-Meteo live weather integration.
- Fetches weather by latitude/longitude.
- Normalizes weather into:
  - temperature_f
  - wind_mph
  - condition
  - visibility_miles
  - source
  - raw_weather_code

route_risk/integrations/routing_client.py
- OSRM routing integration.
- Fetches route from origin coordinate to destination coordinate.
- Uses OSRM public route API.
- Returns:
  - distance_meters
  - duration_seconds
  - full geometry coordinates
  - sampled checkpoints

route_risk/integrations/road_conditions_client.py
- Road-event matching layer.
- Takes route checkpoints and road event dictionaries.
- Uses haversine distance to match nearby events.
- Normalizes event types into scoring-friendly road conditions.
- Supports:
  - construction
  - work zone
  - maintenance
  - restriction
  - road closure
  - icy
  - snowy
  - wet
- Applies most serious nearby event to each checkpoint.

route_risk/integrations/road_event_feed_client.py
- WZDx-style / GeoJSON road-event feed normalizer.
- Can fetch JSON from a URL.
- Can normalize WZDx-style FeatureCollection data into road events.
- Current manual test uses sample WZDx-like data.
- Future next step: add real WZDx feed URL support in the FastAPI endpoint.

Important scripts:

scripts/start_dev.ps1
- Starts the development environment.
- Checks Redis.
- Starts Redis through Docker if needed.
- Starts FastAPI.
- Starts Celery worker.
- On Windows uses Celery solo pool for local stability.

scripts/test_routed_route_risk_api.ps1
- Main current demo/test script.
- Submits Rexburg to Idaho Falls coordinates.
- Sends demo road events:
  - construction near checkpoint 5
  - road closure near checkpoint 6
- Polls until job completion.
- Fetches raw results.
- Fetches clean route-risk summary.
- Prints route blocked fields.

scripts/test_custom_routed_route_risk_api.ps1
- Flexible playground script.
- Lets user quickly edit:
  - origin coordinates
  - destination coordinates
  - checkpoint count
  - fallback road condition
  - road event radius
  - optional road events
  - is_night
- Useful for testing any two routable coordinates.

Other test files that may still exist:

route_risk/testing/manual_test.py
- Early manual scoring tests.
- Useful for checking core scoring still works.

route_risk/testing/manual_celery_task_test.py
- Direct Celery task logic test.
- Does not require Redis/Celery worker because it calls task logic directly.

route_risk/testing/manual_live_weather_scoring_test.py
- Tests live weather fetch plus local scoring.

route_risk/testing/manual_live_weather_celery_task_test.py
- Tests live weather Celery task logic directly.

route_risk/testing/manual_routed_live_weather_test.py
- Important current local integration test.
- Current version should prove:
  - OSRM route generation
  - WZDx-style feed normalization
  - road-event matching
  - live weather
  - route aggregation
  - blocked route behavior

scripts/test_route_risk_api.ps1
- Earlier API test for direct route-risk segments.
- Still useful but less central than the routed endpoint.

scripts/test_route_risk_fanout_api.ps1
- Earlier live-weather fan-out test.
- Useful to demonstrate multi-segment Celery fan-out.
- May become less central now that routed endpoint exists.

Recommended file cleanup later:
Do not delete tests blindly yet. Later, consider creating:
route_risk/testing/archive/

Possible archive candidates:
- older scoring-only experiments
- old fanout-only scripts
- duplicate route-risk API scripts that are replaced by routed API tests

Current recommended commit message:
Add road event matching, WZDx normalization, and blocked route summaries

Current best next technical step:
Add road_event_feed_url support to POST /submit_routed_route_risk_job.

Goal of next step:
Allow API request to include:
road_event_feed_url: "https://some-wzdx-feed-url"

Then FastAPI should:
1. Fetch WZDx-style feed.
2. Normalize feed features into road events.
3. Combine feed events with manually supplied road_events.
4. Match all events to route checkpoints.
5. Score the route.
6. Return route_blocked if closure is found.

Important note:
Do not try to support all lower-48 road feeds at once. Build provider architecture first. WZDx is the preferred direction because it is designed for work-zone data. Some feeds may require keys, and some states may not have good public coverage.

Latest successful test result:
The routed API test with two road events completed successfully. It returned:
- route_status: READY
- route_risk_score: 100
- route_risk_level: Blocked
- route_blocked: True
- average_segment_score: 14
- blocking_segment_count: 1
- highest-risk segment: Route checkpoint 6
- highest-risk road condition: closed
- matched closure event: Demo closure near north Idaho Falls
- summary stated the route is blocked and should not be recommended without rerouting.

Commands to run project:

Start development environment:
.\scripts\start_dev.ps1

Run main routed API demo:
.\scripts\test_routed_route_risk_api.ps1

Run custom coordinate playground:
.\scripts\test_custom_routed_route_risk_api.ps1

Run routed local WZDx-style/manual integration test:
python -m route_risk.testing.manual_routed_live_weather_test

Run core aggregation test:
python -m route_risk.core.aggregation

Run road event feed normalizer test:
python -m route_risk.integrations.road_event_feed_client

Run road condition matcher test:
python -m route_risk.integrations.road_conditions_client

How to use this file in the next chat:
Upload this file to the project files and say:
"Read PROJECT_HANDOFF_ROUTE_RISK_ENGINE.md first. Use it as the source of truth before helping me continue."

