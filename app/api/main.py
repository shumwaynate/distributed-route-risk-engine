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
    matrix_compute_task,
    route_segment_risk_task,
    slow_square_number,
    square_number,
    transient_unreliable_square,
    unreliable_square,
    vector_similarity_task,
)
from route_risk.core.aggregation import aggregate_job_results

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
)

app = FastAPI(
    title="Distributed AI Task Orchestrator",
    description=(
        "Distributed task orchestration prototype using FastAPI, Redis, and Celery. "
        "Now includes Route Risk Engine workloads."
    ),
    version="0.9.0",
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
#
# Route segments now support optional latitude and longitude values.
#
# Why coordinates matter:
# - Weather APIs usually require latitude and longitude.
# - Routing APIs return geographic points along a route.
# - Road-condition data can be matched to nearby route coordinates.
#
# Coordinates are optional during the transition so existing manual tests
# continue to work.
#
# Validation:
# - Latitude must be between -90 and 90.
# - Longitude must be between -180 and 180.


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

    weather: RouteWeatherData
    road_condition: str = "normal"
    is_night: bool = False


class RouteRiskJobRequest(BaseModel):
    route_name: str = "Sample route"
    origin: str = "Unknown origin"
    destination: str = "Unknown destination"
    segments: List[RouteSegmentRequest] = Field(..., min_length=1)


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


def _build_route_risk_summary_response(job_id: str) -> Dict[str, Any]:
    """
    Build a clean user-facing route-risk summary response.

    This is separate from /results/{job_id}, which preserves the original
    orchestrator-style raw task result output.
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

    return {
        "job_id": job_id,
        "route_status": route_status,
        "route_name": metadata.get("route_name"),
        "origin": metadata.get("origin"),
        "destination": metadata.get("destination"),
        "segment_count": metadata.get("segment_count"),
        "coordinate_segment_count": metadata.get("coordinate_segment_count"),
        "route_risk_score": aggregated_route_risk["route_risk_score"],
        "route_risk_level": aggregated_route_risk["route_risk_level"],
        "highest_risk_segment": aggregated_route_risk["highest_risk_segment"],
        "summary": aggregated_route_risk["summary"],
    }


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

    Current version:
    - Accepts route segments directly.
    - Supports optional latitude and longitude values per segment.
    - Submits one route_segment_risk_task per segment.
    - Reuses the existing Redis job tracking system.
    - Reuses the existing /job_status/{job_id} endpoint.
    - Reuses the existing /results/{job_id} endpoint.
    - Supports clean summary retrieval through /route_risk_summary/{job_id}.
    """

    task_ids = []
    segments = [segment.model_dump() for segment in request.segments]

    coordinate_segment_count = sum(
        1
        for segment in segments
        if segment.get("latitude") is not None
        and segment.get("longitude") is not None
    )

    for index, segment in enumerate(segments, start=1):
        task = route_segment_risk_task.delay(
            index,
            segment,
        )
        task_ids.append(task.id)

    job_id = _create_job(
        workload="route_risk_segments",
        task_ids=task_ids,
        metadata={
            "route_name": request.route_name,
            "origin": request.origin,
            "destination": request.destination,
            "segment_count": len(segments),
            "coordinate_segment_count": coordinate_segment_count,
            "prototype_stage": "manual_segments_with_optional_coordinates",
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
        "summary_endpoint": f"/route_risk_summary/{job_id}",
    }


@app.get("/route_risk_summary/{job_id}")
def route_risk_summary(job_id: str) -> Dict[str, Any]:
    """
    Return a clean user-facing route-risk summary.

    This endpoint is intentionally simpler than /results/{job_id}.

    Use this when the user/demo should see:
    - route name
    - origin
    - destination
    - total segment count
    - coordinate-enabled segment count
    - route risk score
    - route risk level
    - highest-risk segment
    - readable summary

    Use /results/{job_id} when debugging or demonstrating raw Celery task output.
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