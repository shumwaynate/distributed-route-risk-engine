import math
import os
import random
import time
from typing import Any, Dict, List, Union

# Keep NumPy from using multiple internal threads per Celery worker process.
# This makes worker-count scaling tests cleaner and easier to interpret.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np

from app.worker.celery_app import celery_app
from route_risk.core.scoring import score_route, score_segment
from route_risk.integrations.weather_client import fetch_weather_for_coordinate


# ============================================================
# ORIGINAL ORCHESTRATOR LOGIC
# ============================================================
#
# These tasks belong to the original Distributed AI Task Orchestrator.
#
# They are being preserved because they demonstrate:
# - FastAPI / Redis / Celery distributed execution
# - deterministic task processing
# - retry behavior
# - failure handling
# - benchmarkable CPU workloads
# - scaling experiments
#
# The Route Risk Engine pivot should build on this infrastructure instead
# of deleting it.


@celery_app.task
def square_number(x: int) -> int:
    """
    Basic deterministic task used for simple API testing.
    """
    return x * x


@celery_app.task
def slow_square_number(x: int, delay_seconds: float = 1.0) -> int:
    """
    Controlled delay workload used for baseline scaling tests.

    This is useful because each task takes a predictable amount of time,
    making worker scaling easy to measure.
    """
    time.sleep(delay_seconds)
    return x * x


@celery_app.task(bind=True, max_retries=3)
def unreliable_square(self, x: int, fail_on_even: bool = True) -> int:
    """
    Permanent failure test task.

    If fail_on_even is true, even numbers intentionally fail. Celery retries the
    task up to max_retries, but because the failure condition never changes,
    even-numbered tasks eventually end in FAILURE.

    This is useful for proving that the system can detect failed tasks and
    report PARTIAL_FAILURE at the job level.
    """
    try:
        if fail_on_even and x % 2 == 0:
            raise ValueError(f"Intentional failure for even number: {x}")

        return x * x

    except Exception as exc:
        raise self.retry(exc=exc, countdown=1)


@celery_app.task(bind=True, max_retries=3)
def transient_unreliable_square(
    self,
    x: int,
    fail_attempts: int = 2,
) -> Dict[str, Union[int, str]]:
    """
    Transient failure test task.

    This task intentionally fails for the first fail_attempts attempts, then
    succeeds on a later retry.
    """
    current_retry_count = self.request.retries
    current_attempt_number = current_retry_count + 1

    try:
        if current_retry_count < fail_attempts:
            raise ValueError(
                f"Transient failure for {x} on attempt {current_attempt_number}"
            )

        return {
            "input": x,
            "output": x * x,
            "workload": "transient_unreliable",
            "attempts": current_attempt_number,
            "retries_used": current_retry_count,
            "status": "succeeded_after_retry",
        }

    except Exception as exc:
        raise self.retry(exc=exc, countdown=1)


def _deterministic_vector(seed: int, size: int) -> List[float]:
    """
    Creates a deterministic pseudo-random vector.
    """
    rng = random.Random(seed)
    return [rng.random() for _ in range(size)]


def _dot_product(a: List[float], b: List[float]) -> float:
    """
    Computes the dot product of two vectors.
    """
    return sum(x * y for x, y in zip(a, b))


@celery_app.task
def vector_similarity_task(
    task_id: int,
    vector_size: int = 1000,
) -> Dict[str, float]:
    """
    AI-style deterministic vector similarity workload.
    """
    vector_a = _deterministic_vector(task_id, vector_size)
    vector_b = _deterministic_vector(task_id + 10_000, vector_size)

    dot = _dot_product(vector_a, vector_b)
    magnitude_a = math.sqrt(_dot_product(vector_a, vector_a))
    magnitude_b = math.sqrt(_dot_product(vector_b, vector_b))

    if magnitude_a == 0 or magnitude_b == 0:
        cosine_similarity = 0.0
    else:
        cosine_similarity = dot / (magnitude_a * magnitude_b)

    return {
        "task_id": task_id,
        "workload": "vector",
        "vector_size": vector_size,
        "cosine_similarity": round(cosine_similarity, 8),
        "checksum": round(dot, 8),
    }


def _matrix_iterations_for_size(matrix_size: int) -> int:
    """
    Chooses a repeat count for the matrix workload.
    """
    if matrix_size <= 250:
        return 160

    if matrix_size <= 300:
        return 120

    if matrix_size <= 400:
        return 80

    if matrix_size <= 500:
        return 60

    if matrix_size <= 700:
        return 40

    return 25


@celery_app.task
def matrix_compute_task(
    task_id: int,
    matrix_size: int = 700,
) -> Dict[str, float]:
    """
    AI-style deterministic NumPy matrix compute workload.
    """
    rng = np.random.default_rng(seed=task_id)

    matrix_a = rng.random((matrix_size, matrix_size), dtype=np.float64)
    matrix_b = rng.random((matrix_size, matrix_size), dtype=np.float64)

    iterations = _matrix_iterations_for_size(matrix_size)
    checksum = 0.0

    for _ in range(iterations):
        result_matrix = matrix_a @ matrix_b
        checksum += float(np.sum(result_matrix))

    return {
        "task_id": task_id,
        "workload": "matrix",
        "matrix_size": matrix_size,
        "iterations": iterations,
        "checksum": round(checksum, 8),
    }


# ============================================================
# ROUTE RISK ENGINE LOGIC
# ============================================================
#
# These tasks belong to the Route Risk / Driving Recommendation Engine.
#
# Current stage:
# - Supports manual and live-weather route-risk tasks.
# - Supports optional latitude and longitude values.
# - Scores route segments independently.
# - Preserves coordinates in results.
# - Preserves road-condition matching details.
# - Preserves route identity for multi-route comparison.
#
# Each route checkpoint can be processed independently, making the workload
# suitable for distributed Celery workers.


def _build_road_context_result(segment: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract road-condition context from a route segment.

    This keeps manual segment tests, live-weather tasks, and routed/event-enriched
    tasks returning a consistent result shape.
    """

    return {
        "road_condition": segment.get("road_condition", "normal"),
        "road_condition_source": segment.get(
            "road_condition_source",
            "request",
        ),
        "matched_road_event": segment.get("matched_road_event"),
        "nearby_road_event_count": segment.get(
            "nearby_road_event_count",
            0,
        ),
    }


def _build_route_context_result(segment: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract optional multi-route identity information from a segment.

    Existing single-route jobs may not include these values. In that case,
    both fields remain None and existing behavior is preserved.

    Multi-route ORS jobs will provide these fields so completed checkpoint
    results can be grouped back into the correct route.
    """

    return {
        "route_id": segment.get("route_id"),
        "route_label": segment.get("route_label"),
    }


@celery_app.task
def route_segment_risk_task(
    task_id: int,
    segment: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Score a single route segment inside a Celery worker using provided weather.

    This is the manual-weather route-risk task.

    Each route segment can be processed independently, making it a good fit
    for distributed Celery workers.

    Optional coordinates, route identity, and road-event details are preserved
    in the output for explanation, grouping, and debugging.
    """

    road_context = _build_road_context_result(segment)
    route_context = _build_route_context_result(segment)

    segment_result = score_segment(
        weather=segment.get("weather", {}),
        road_condition=road_context["road_condition"],
        is_night=segment.get("is_night", False),
    )

    return {
        "task_id": task_id,
        "workload": "route_segment_risk",
        "weather_mode": "manual",
        **route_context,
        "segment_label": segment.get("label", "Unnamed segment"),
        "latitude": segment.get("latitude"),
        "longitude": segment.get("longitude"),
        **road_context,
        "risk_score": segment_result["risk_score"],
        "risk_level": segment_result["risk_level"],
        "factors": segment_result["factors"],
    }


@celery_app.task
def live_weather_route_segment_risk_task(
    task_id: int,
    segment: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Score a single route segment inside a Celery worker using live weather.

    Flow:
    - Receive a route segment with latitude and longitude.
    - Preserve route identity and road-condition context.
    - Fetch live weather from Open-Meteo.
    - Normalize weather data.
    - Score the segment using the existing scoring function.
    - Return route identity, coordinates, weather, road context, and risk result.

    This task requires:
    - Internet access.
    - Valid latitude and longitude.
    - Open-Meteo API availability.
    """

    latitude = segment.get("latitude")
    longitude = segment.get("longitude")

    if latitude is None or longitude is None:
        raise ValueError(
            "live_weather_route_segment_risk_task requires both "
            "latitude and longitude."
        )

    road_context = _build_road_context_result(segment)
    route_context = _build_route_context_result(segment)

    live_weather = fetch_weather_for_coordinate(
        latitude=float(latitude),
        longitude=float(longitude),
    )

    segment_result = score_segment(
        weather=live_weather,
        road_condition=road_context["road_condition"],
        is_night=segment.get("is_night", False),
    )

    return {
        "task_id": task_id,
        "workload": "route_segment_risk",
        "weather_mode": "live",
        **route_context,
        "segment_label": segment.get("label", "Unnamed segment"),
        "latitude": latitude,
        "longitude": longitude,
        "weather": live_weather,
        **road_context,
        "risk_score": segment_result["risk_score"],
        "risk_level": segment_result["risk_level"],
        "factors": segment_result["factors"],
    }


@celery_app.task
def route_risk_summary_task(
    task_id: int,
    segments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Score a full route inside a Celery worker.

    This task is retained for direct testing and early prototype comparisons.
    The main distributed API route currently uses one route segment task per
    checkpoint.
    """

    route_result = score_route(segments)

    return {
        "task_id": task_id,
        "workload": "route_risk_summary",
        "route_risk_score": route_result["route_risk_score"],
        "route_risk_level": route_result["route_risk_level"],
        "segment_results": route_result["segment_results"],
        "summary": route_result["summary"],
    }