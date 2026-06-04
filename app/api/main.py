import json
import os
from typing import Any, Dict, List, Optional
from uuid import uuid4

import redis
from celery.result import AsyncResult
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.worker.celery_app import celery_app
from app.worker.tasks import (
    live_weather_route_segment_risk_task,
    matrix_compute_task,
    route_segment_risk_task,
    slow_square_number,
    square_number,
    transient_unreliable_square,
    unreliable_square,
    vector_similarity_task,
)
from route_risk.core.aggregation import aggregate_job_results
from route_risk.integrations.road_conditions_client import (
    apply_road_conditions_to_checkpoints,
)
from route_risk.integrations.routing_client import fetch_route_between_coordinates

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
)

app = FastAPI(
    title="Distributed AI Task Orchestrator",
    description=(
        "Distributed task orchestration prototype using FastAPI, Redis, and Celery. "
        "Now includes Route Risk Engine workloads with optional live weather, "
        "OSRM route generation, and road-event matching."
    ),
    version="1.2.0",
)


# ============================================================
# ORIGINAL ORCHESTRATOR REQUEST MODELS
# ============================================================

class NumberBatchRequest(BaseModel):
    numbers: List[int] = Field(..., min_length=1)


class SlowBatchRequest(BaseModel):
    numbers: List[int] = Field(..., min_length=1)
    delay_seconds: float = Field(1.0, ge=0)


class UnreliableBatchRequest(BaseModel):
    numbers: List[int] = Field(..., min_length=1)
    fail_on_even: bool = True


class TransientUnreliableBatchRequest(BaseModel):
    numbers: List[int] = Field(..., min_length=1)
    fail_attempts: int = Field(2, ge=0, le=3)


class MatrixBatchRequest(BaseModel):
    task_count: int = Field(20, ge=1)
    matrix_size: int = Field(700, ge=1)


class VectorBatchRequest(BaseModel):
    task_count: int = Field(20, ge=1)
    vector_size: int = Field(1000, ge=1)


# ============================================================
# ROUTE RISK ENGINE REQUEST MODELS
# ============================================================

class RouteWeatherData(BaseModel):
    temperature_f: Optional[float] = None
    wind_mph: Optional[float] = None
    condition: str = "clear"
    visibility_miles: Optional[float] = None


class RouteSegmentRequest(BaseModel):
    label: str = "Unnamed segment"

    latitude: Optional[float] = Field(
        default=None,
        ge=-90,
        le=90,
        description="Latitude of the route segment analysis point.",
    )

    longitude: Optional[float] = Field(
        default=None,
        ge=-180,
        le=180,
        description="Longitude of the route segment analysis point.",
    )

    weather: RouteWeatherData = Field(
        default_factory=RouteWeatherData,
        description=(
            "Manual weather data. Used when use_live_weather is false. "
            "Ignored for scoring when use_live_weather is true."
        ),
    )

    road_condition: str = "normal"
    is_night: bool = False


class RouteRiskJobRequest(BaseModel):
    route_name: str = "Sample route"
    origin: str = "Unknown origin"
    destination: str = "Unknown destination"

    use_live_weather: bool = Field(
        default=False,
        description=(
            "If true, workers fetch live weather using latitude/longitude. "
            "If false, workers use weather data provided in the request."
        ),
    )

    segments: List[RouteSegmentRequest] = Field(..., min_length=1)


class RoadEventRequest(BaseModel):
    event_id: str = "road-event"
    event_type: str = Field(
        default="construction",
        description=(
            "Road event type such as construction, work zone, road closure, icy, snowy, or wet."
        ),
    )
    description: str = ""
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    source: str = "request-road-event"


class RoutedRouteRiskJobRequest(BaseModel):
    route_name: str = "Generated route risk job"

    origin_label: str = "Origin"
    origin_latitude: float = Field(..., ge=-90, le=90)
    origin_longitude: float = Field(..., ge=-180, le=180)

    destination_label: str = "Destination"
    destination_latitude: float = Field(..., ge=-90, le=90)
    destination_longitude: float = Field(..., ge=-180, le=180)

    checkpoint_count: int = Field(
        default=8,
        ge=2,
        le=50,
        description="Number of sampled checkpoints to analyze along the generated route.",
    )

    road_condition: str = Field(
        default="normal",
        description=(
            "Fallback route-wide road condition used when no road event matches a checkpoint."
        ),
    )

    road_event_radius_miles: float = Field(
        default=2.0,
        ge=0.1,
        le=25.0,
        description="Search radius for matching supplied road events to route checkpoints.",
    )

    road_events: List[RoadEventRequest] = Field(
        default_factory=list,
        description=(
            "Optional road events to match against generated route checkpoints. "
            "This prepares the API for WZDx / 511 / DOT feed results."
        ),
    )

    is_night: bool = Field(
        default=False,
        description="Whether the route should be scored as nighttime travel.",
    )


# ============================================================
# SHARED JOB HELPERS
# ============================================================

def _job_key(job_id: str) -> str:
    return f"job:{job_id}"


def _create_job(
    workload: str,
    task_ids: List[str],
    metadata: Dict[str, Any],
) -> str:
    job_id = str(uuid4())

    redis_client.hset(
        _job_key(job_id),
        mapping={
            "job_id": job_id,
            "workload": workload,
            "task_ids": json.dumps(task_ids),
            "total_tasks": len(task_ids),
            "metadata": json.dumps(metadata),
        },
    )

    return job_id


def _load_job(job_id: str) -> Dict[str, Any]:
    job_data = redis_client.hgetall(_job_key(job_id))

    if not job_data:
        raise HTTPException(status_code=404, detail="Job not found")

    return job_data


def _get_task_results(task_ids: List[str]) -> List[Dict[str, Any]]:
    results = []

    for task_id in task_ids:
        async_result = AsyncResult(task_id, app=celery_app)

        task_info = {
            "task_id": task_id,
            "status": async_result.status,
            "result": None,
            "error": None,
        }

        if async_result.successful():
            task_info["result"] = async_result.result
        elif async_result.failed():
            task_info["error"] = str(async_result.result)

        results.append(task_info)

    return results


def _validate_live_weather_segments(segments: List[Dict[str, Any]]) -> None:
    """
    Ensure every segment has coordinates before using live weather mode.
    """

    missing_coordinate_labels = []

    for segment in segments:
        if segment.get("latitude") is None or segment.get("longitude") is None:
            missing_coordinate_labels.append(segment.get("label", "Unnamed segment"))

    if missing_coordinate_labels:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "Live weather mode requires latitude and longitude for every segment."
                ),
                "segments_missing_coordinates": missing_coordinate_labels,
            },
        )


def _build_route_risk_summary_response(job_id: str) -> Dict[str, Any]:
    """
    Build a clean user-facing route-risk summary response.

    This is separate from /results/{job_id}, which preserves the original
    orchestrator-style raw task result output.

    For route-risk jobs, this response includes:
    - route_blocked
    - route_warning
    - average_segment_score
    - blocking_segments
    - highest_risk_segment
    """

    job_data = _load_job(job_id)
    workload = job_data.get("workload", "unknown")

    if workload != "route_risk_segments":
        raise HTTPException(
            status_code=400,
            detail=(
                "This endpoint only supports route_risk_segments jobs. "
                f"Job workload was: {workload}"
            ),
        )

    task_ids = json.loads(job_data["task_ids"])
    task_results = _get_task_results(task_ids)
    metadata = json.loads(job_data.get("metadata", "{}"))
    aggregated_route_risk = aggregate_job_results(task_results)

    incomplete_tasks = [
        task_result
        for task_result in task_results
        if task_result.get("status") != "SUCCESS"
    ]

    if incomplete_tasks:
        route_status = "INCOMPLETE"
    else:
        route_status = "READY"

    blocking_segments = aggregated_route_risk.get("blocking_segments", [])

    return {
        "job_id": job_id,
        "route_status": route_status,
        "route_name": metadata.get("route_name"),
        "origin": metadata.get("origin"),
        "destination": metadata.get("destination"),

        # Route generation metadata.
        "segment_count": metadata.get("segment_count"),
        "coordinate_segment_count": metadata.get("coordinate_segment_count"),
        "weather_mode": metadata.get("weather_mode"),
        "route_source": metadata.get("route_source"),
        "distance_meters": metadata.get("distance_meters"),
        "duration_seconds": metadata.get("duration_seconds"),
        "geometry_point_count": metadata.get("geometry_point_count"),
        "checkpoint_count": metadata.get("checkpoint_count"),

        # Road-event metadata.
        "road_event_count": metadata.get("road_event_count"),
        "matched_road_event_checkpoint_count": metadata.get(
            "matched_road_event_checkpoint_count"
        ),

        # Route-level risk summary.
        "route_risk_score": aggregated_route_risk["route_risk_score"],
        "route_risk_level": aggregated_route_risk["route_risk_level"],
        "route_blocked": aggregated_route_risk.get("route_blocked", False),
        "route_warning": aggregated_route_risk.get("route_warning"),
        "average_segment_score": aggregated_route_risk.get("average_segment_score"),
        "highest_risk_segment": aggregated_route_risk["highest_risk_segment"],
        "blocking_segments": blocking_segments,
        "blocking_segment_count": len(blocking_segments),
        "incomplete_task_count": aggregated_route_risk.get(
            "incomplete_task_count",
            0,
        ),
        "summary": aggregated_route_risk["summary"],
    }


def _build_segments_from_routed_checkpoints(
    checkpoints: List[Dict[str, Any]],
    road_condition: str,
    is_night: bool,
) -> List[Dict[str, Any]]:
    """
    Convert OSRM sampled checkpoints into route-risk segment dictionaries.

    The generated segments intentionally match the structure used by the
    existing manual route-risk endpoint so the same Celery task pipeline can
    process them.
    """

    segments = []

    for checkpoint in checkpoints:
        segments.append(
            {
                "label": checkpoint.get("label", "Route checkpoint"),
                "latitude": checkpoint["latitude"],
                "longitude": checkpoint["longitude"],
                "weather": {
                    "temperature_f": 0,
                    "wind_mph": 0,
                    "condition": "ignored because live weather is enabled",
                    "visibility_miles": None,
                },
                "road_condition": checkpoint.get("road_condition", road_condition),
                "road_condition_source": checkpoint.get(
                    "road_condition_source",
                    "fallback",
                ),
                "matched_road_event": checkpoint.get("matched_road_event"),
                "nearby_road_event_count": checkpoint.get(
                    "nearby_road_event_count",
                    0,
                ),
                "is_night": is_night,
            }
        )

    return segments


# ============================================================
# ROOT / HEALTH ENDPOINT
# ============================================================

@app.get("/")
def root() -> Dict[str, str]:
    return {
        "message": "Distributed AI Task Orchestrator API is running",
        "route_risk_status": "Route Risk Engine prototype endpoints are available",
    }


# ============================================================
# ORIGINAL ORCHESTRATOR ENDPOINTS
# ============================================================

@app.post("/submit_batch")
def submit_batch(request: NumberBatchRequest) -> Dict[str, Any]:
    task_ids = []

    for number in request.numbers:
        task = square_number.delay(number)
        task_ids.append(task.id)

    job_id = _create_job(
        workload="square",
        task_ids=task_ids,
        metadata={
            "numbers": request.numbers,
        },
    )

    return {
        "message": "Batch submitted",
        "job_id": job_id,
        "task_count": len(task_ids),
        "workload": "square",
    }


@app.post("/submit_slow_batch")
def submit_slow_batch(request: SlowBatchRequest) -> Dict[str, Any]:
    task_ids = []

    for number in request.numbers:
        task = slow_square_number.delay(number, request.delay_seconds)
        task_ids.append(task.id)

    job_id = _create_job(
        workload="slow",
        task_ids=task_ids,
        metadata={
            "numbers": request.numbers,
            "delay_seconds": request.delay_seconds,
        },
    )

    return {
        "message": "Slow batch submitted",
        "job_id": job_id,
        "task_count": len(task_ids),
        "workload": "slow",
        "delay_seconds": request.delay_seconds,
    }


@app.post("/submit_unreliable_batch")
def submit_unreliable_batch(request: UnreliableBatchRequest) -> Dict[str, Any]:
    task_ids = []

    for number in request.numbers:
        task = unreliable_square.delay(number, request.fail_on_even)
        task_ids.append(task.id)

    job_id = _create_job(
        workload="unreliable",
        task_ids=task_ids,
        metadata={
            "numbers": request.numbers,
            "fail_on_even": request.fail_on_even,
            "failure_type": "permanent_when_even",
        },
    )

    return {
        "message": "Unreliable batch submitted",
        "job_id": job_id,
        "task_count": len(task_ids),
        "workload": "unreliable",
        "fail_on_even": request.fail_on_even,
        "failure_type": "permanent_when_even",
    }


@app.post("/submit_transient_unreliable_batch")
def submit_transient_unreliable_batch(
    request: TransientUnreliableBatchRequest,
) -> Dict[str, Any]:
    task_ids = []

    for number in request.numbers:
        task = transient_unreliable_square.delay(number, request.fail_attempts)
        task_ids.append(task.id)

    job_id = _create_job(
        workload="transient_unreliable",
        task_ids=task_ids,
        metadata={
            "numbers": request.numbers,
            "fail_attempts": request.fail_attempts,
            "failure_type": "transient_then_success",
        },
    )

    return {
        "message": "Transient unreliable batch submitted",
        "job_id": job_id,
        "task_count": len(task_ids),
        "workload": "transient_unreliable",
        "fail_attempts": request.fail_attempts,
        "failure_type": "transient_then_success",
    }


@app.post("/submit_matrix_batch")
def submit_matrix_batch(request: MatrixBatchRequest) -> Dict[str, Any]:
    task_ids = []

    for task_number in range(1, request.task_count + 1):
        task = matrix_compute_task.delay(task_number, request.matrix_size)
        task_ids.append(task.id)

    job_id = _create_job(
        workload="matrix",
        task_ids=task_ids,
        metadata={
            "task_count": request.task_count,
            "matrix_size": request.matrix_size,
        },
    )

    return {
        "message": "Matrix compute batch submitted",
        "job_id": job_id,
        "task_count": len(task_ids),
        "workload": "matrix",
        "matrix_size": request.matrix_size,
    }


@app.post("/submit_vector_batch")
def submit_vector_batch(request: VectorBatchRequest) -> Dict[str, Any]:
    task_ids = []

    for task_number in range(1, request.task_count + 1):
        task = vector_similarity_task.delay(task_number, request.vector_size)
        task_ids.append(task.id)

    job_id = _create_job(
        workload="vector",
        task_ids=task_ids,
        metadata={
            "task_count": request.task_count,
            "vector_size": request.vector_size,
        },
    )

    return {
        "message": "Vector similarity batch submitted",
        "job_id": job_id,
        "task_count": len(task_ids),
        "workload": "vector",
        "vector_size": request.vector_size,
    }


# ============================================================
# ROUTE RISK ENGINE ENDPOINTS
# ============================================================

@app.post("/submit_route_risk_job")
def submit_route_risk_job(request: RouteRiskJobRequest) -> Dict[str, Any]:
    """
    Submit a distributed Route Risk Engine job.

    This endpoint accepts route segments directly.
    """

    task_ids = []
    segments = [segment.model_dump() for segment in request.segments]

    coordinate_segment_count = sum(
        1
        for segment in segments
        if segment.get("latitude") is not None
        and segment.get("longitude") is not None
    )

    if request.use_live_weather:
        _validate_live_weather_segments(segments)

    for index, segment in enumerate(segments, start=1):
        if request.use_live_weather:
            task = live_weather_route_segment_risk_task.delay(
                index,
                segment,
            )
        else:
            task = route_segment_risk_task.delay(
                index,
                segment,
            )

        task_ids.append(task.id)

    weather_mode = "live" if request.use_live_weather else "manual"

    job_id = _create_job(
        workload="route_risk_segments",
        task_ids=task_ids,
        metadata={
            "route_name": request.route_name,
            "origin": request.origin,
            "destination": request.destination,
            "segment_count": len(segments),
            "coordinate_segment_count": coordinate_segment_count,
            "weather_mode": weather_mode,
            "prototype_stage": f"{weather_mode}_weather_segments",
        },
    )

    return {
        "message": "Distributed route risk job submitted",
        "job_id": job_id,
        "task_count": len(task_ids),
        "workload": "route_risk_segments",
        "route_name": request.route_name,
        "origin": request.origin,
        "destination": request.destination,
        "segment_count": len(segments),
        "coordinate_segment_count": coordinate_segment_count,
        "weather_mode": weather_mode,
        "summary_endpoint": f"/route_risk_summary/{job_id}",
    }


@app.post("/submit_routed_route_risk_job")
def submit_routed_route_risk_job(request: RoutedRouteRiskJobRequest) -> Dict[str, Any]:
    """
    Submit a routed live-weather Route Risk Engine job.

    This endpoint:
    - Receives origin and destination coordinates.
    - Calls OSRM to generate a real route.
    - Samples checkpoints along the route.
    - Optionally matches supplied road events to checkpoints.
    - Submits one live-weather Celery task per checkpoint.
    - Reuses the existing Redis job tracking and summary endpoints.
    """

    try:
        route = fetch_route_between_coordinates(
            origin_latitude=request.origin_latitude,
            origin_longitude=request.origin_longitude,
            destination_latitude=request.destination_latitude,
            destination_longitude=request.destination_longitude,
            checkpoint_count=request.checkpoint_count,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Failed to generate route from routing provider.",
                "error": str(exc),
            },
        ) from exc

    road_events = [event.model_dump() for event in request.road_events]

    enriched_checkpoints = apply_road_conditions_to_checkpoints(
        checkpoints=route["checkpoints"],
        road_events=road_events,
        radius_miles=request.road_event_radius_miles,
        fallback_road_condition=request.road_condition,
    )

    matched_road_event_checkpoint_count = sum(
        1
        for checkpoint in enriched_checkpoints
        if checkpoint.get("matched_road_event") is not None
    )

    segments = _build_segments_from_routed_checkpoints(
        checkpoints=enriched_checkpoints,
        road_condition=request.road_condition,
        is_night=request.is_night,
    )

    task_ids = []

    for index, segment in enumerate(segments, start=1):
        task = live_weather_route_segment_risk_task.delay(
            index,
            segment,
        )
        task_ids.append(task.id)

    origin_text = request.origin_label
    destination_text = request.destination_label

    job_id = _create_job(
        workload="route_risk_segments",
        task_ids=task_ids,
        metadata={
            "route_name": request.route_name,
            "origin": origin_text,
            "destination": destination_text,
            "origin_latitude": request.origin_latitude,
            "origin_longitude": request.origin_longitude,
            "destination_latitude": request.destination_latitude,
            "destination_longitude": request.destination_longitude,
            "segment_count": len(segments),
            "coordinate_segment_count": len(segments),
            "weather_mode": "live",
            "route_source": route["source"],
            "distance_meters": route["distance_meters"],
            "duration_seconds": route["duration_seconds"],
            "geometry_point_count": route["geometry_point_count"],
            "checkpoint_count": route["checkpoint_count"],
            "fallback_road_condition": request.road_condition,
            "road_event_radius_miles": request.road_event_radius_miles,
            "road_event_count": len(road_events),
            "matched_road_event_checkpoint_count": matched_road_event_checkpoint_count,
            "is_night": request.is_night,
            "prototype_stage": "osrm_routed_live_weather_road_event_segments",
        },
    )

    return {
        "message": "Routed live-weather route risk job submitted",
        "job_id": job_id,
        "task_count": len(task_ids),
        "workload": "route_risk_segments",
        "route_name": request.route_name,
        "origin": origin_text,
        "destination": destination_text,
        "segment_count": len(segments),
        "coordinate_segment_count": len(segments),
        "weather_mode": "live",
        "route_source": route["source"],
        "distance_meters": route["distance_meters"],
        "duration_seconds": route["duration_seconds"],
        "geometry_point_count": route["geometry_point_count"],
        "checkpoint_count": route["checkpoint_count"],
        "road_event_count": len(road_events),
        "matched_road_event_checkpoint_count": matched_road_event_checkpoint_count,
        "summary_endpoint": f"/route_risk_summary/{job_id}",
    }


@app.get("/route_risk_summary/{job_id}")
def route_risk_summary(job_id: str) -> Dict[str, Any]:
    """
    Return a clean user-facing route-risk summary.

    This endpoint is intentionally simpler than /results/{job_id}.
    """

    return _build_route_risk_summary_response(job_id)


# ============================================================
# SHARED JOB STATUS / RESULTS ENDPOINTS
# ============================================================

@app.get("/job_status/{job_id}")
def job_status(job_id: str) -> Dict[str, Any]:
    job_data = _load_job(job_id)

    task_ids = json.loads(job_data["task_ids"])
    total_tasks = int(job_data["total_tasks"])

    completed_tasks = 0
    failed_tasks = 0
    pending_tasks = 0
    running_tasks = 0
    retrying_tasks = 0

    for task_id in task_ids:
        async_result = AsyncResult(task_id, app=celery_app)

        if async_result.successful():
            completed_tasks += 1
        elif async_result.failed():
            failed_tasks += 1
        elif async_result.status == "RETRY":
            retrying_tasks += 1
        elif async_result.status == "PENDING":
            pending_tasks += 1
        else:
            running_tasks += 1

    unfinished_tasks = pending_tasks + running_tasks + retrying_tasks
    finished_tasks = completed_tasks + failed_tasks
    progress_percent = round((finished_tasks / total_tasks) * 100, 2)

    if failed_tasks > 0 and finished_tasks == total_tasks:
        status = "PARTIAL_FAILURE"
    elif completed_tasks == total_tasks:
        status = "SUCCESS"
    elif unfinished_tasks > 0 or finished_tasks > 0:
        status = "IN_PROGRESS"
    else:
        status = "PENDING"

    return {
        "job_id": job_id,
        "workload": job_data.get("workload", "unknown"),
        "status": status,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "failed_tasks": failed_tasks,
        "pending_tasks": pending_tasks,
        "running_tasks": running_tasks,
        "retrying_tasks": retrying_tasks,
        "progress_percent": progress_percent,
        "metadata": json.loads(job_data.get("metadata", "{}")),
    }


@app.get("/results/{job_id}")
def results(job_id: str) -> Dict[str, Any]:
    job_data = _load_job(job_id)
    task_ids = json.loads(job_data["task_ids"])

    task_results = _get_task_results(task_ids)
    workload = job_data.get("workload", "unknown")

    response = {
        "job_id": job_id,
        "workload": workload,
        "metadata": json.loads(job_data.get("metadata", "{}")),
        "results": task_results,
    }

    # ============================================================
    # ROUTE RISK ENGINE RESULT AGGREGATION
    # ============================================================
    #
    # The original orchestrator returns raw task results.
    #
    # For route-risk jobs, we also add an aggregated route-level summary so
    # users can see one overall risk score, highest-risk segment, and summary.
    #
    # Raw segment results are still preserved above in "results".

    if workload == "route_risk_segments":
        response["aggregated_route_risk"] = aggregate_job_results(task_results)

    return response