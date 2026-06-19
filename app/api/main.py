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
from route_risk.integrations.ors_client import fetch_ors_alternative_routes
from route_risk.integrations.road_conditions_client import (
    apply_road_conditions_to_checkpoints,
)
from route_risk.integrations.routing_client import (
    fetch_route_between_coordinates,
)
from route_risk.integrations.state_511_clients.state_event_loader import (
    fetch_state_event_groups,
    normalize_state_codes,
)


REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0",
)

redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
)

app = FastAPI(
    title="Distributed Route Risk Engine",
    description=(
        "Distributed task orchestration prototype using FastAPI, Redis, "
        "and Celery. Includes single-route OSRM analysis, multi-route "
        "OpenRouteService comparison, live weather, manually supplied road "
        "events, and optional state 511 roadway events."
    ),
    version="1.5.0",
)


# ============================================================
# ORIGINAL ORCHESTRATOR REQUEST MODELS
# ============================================================

class NumberBatchRequest(BaseModel):
    numbers: List[int] = Field(
        ...,
        min_length=1,
    )


class SlowBatchRequest(BaseModel):
    numbers: List[int] = Field(
        ...,
        min_length=1,
    )

    delay_seconds: float = Field(
        1.0,
        ge=0,
    )


class UnreliableBatchRequest(BaseModel):
    numbers: List[int] = Field(
        ...,
        min_length=1,
    )

    fail_on_even: bool = True


class TransientUnreliableBatchRequest(BaseModel):
    numbers: List[int] = Field(
        ...,
        min_length=1,
    )

    fail_attempts: int = Field(
        2,
        ge=0,
        le=3,
    )


class MatrixBatchRequest(BaseModel):
    task_count: int = Field(
        20,
        ge=1,
    )

    matrix_size: int = Field(
        700,
        ge=1,
    )


class VectorBatchRequest(BaseModel):
    task_count: int = Field(
        20,
        ge=1,
    )

    vector_size: int = Field(
        1000,
        ge=1,
    )


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
            "Manual weather data used when use_live_weather is false. "
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
            "When true, workers fetch live weather using each segment's "
            "latitude and longitude."
        ),
    )

    segments: List[RouteSegmentRequest] = Field(
        ...,
        min_length=1,
    )


class RoadEventRequest(BaseModel):
    event_id: str = "road-event"

    event_type: str = Field(
        default="construction",
        description=(
            "Road event type such as construction, road closure, incident, "
            "icy, snowy, or wet."
        ),
    )

    description: str = ""

    latitude: float = Field(
        ...,
        ge=-90,
        le=90,
    )

    longitude: float = Field(
        ...,
        ge=-180,
        le=180,
    )

    source: str = "request-road-event"


class RoutedRouteRiskJobRequest(BaseModel):
    route_name: str = "Generated route risk job"

    origin_label: str = "Origin"

    origin_latitude: float = Field(
        ...,
        ge=-90,
        le=90,
    )

    origin_longitude: float = Field(
        ...,
        ge=-180,
        le=180,
    )

    destination_label: str = "Destination"

    destination_latitude: float = Field(
        ...,
        ge=-90,
        le=90,
    )

    destination_longitude: float = Field(
        ...,
        ge=-180,
        le=180,
    )

    checkpoint_count: int = Field(
        default=8,
        ge=2,
        le=50,
        description=(
            "Number of sampled checkpoints to analyze along each route."
        ),
    )

    road_condition: str = Field(
        default="normal",
        description=(
            "Fallback road condition used when no road event matches a "
            "checkpoint."
        ),
    )

    road_event_radius_miles: float = Field(
        default=2.0,
        ge=0.1,
        le=25.0,
        description=(
            "Search radius used when matching road events to checkpoints."
        ),
    )

    road_events: List[RoadEventRequest] = Field(
        default_factory=list,
        description=(
            "Optional manually supplied road events to match against route "
            "checkpoints."
        ),
    )

    is_night: bool = Field(
        default=False,
        description="Whether the route should be scored as nighttime travel.",
    )


class RouteComparisonJobRequest(RoutedRouteRiskJobRequest):
    target_route_count: int = Field(
        default=3,
        ge=2,
        le=3,
        description=(
            "Number of OpenRouteService route candidates to request."
        ),
    )

    share_factor: float = Field(
        default=0.6,
        gt=0,
        le=1,
        description=(
            "Maximum route overlap accepted by OpenRouteService."
        ),
    )

    weight_factor: float = Field(
        default=2.0,
        ge=1,
        le=3,
        description=(
            "Maximum allowed alternative-route cost relative to the "
            "primary route."
        ),
    )

    use_live_state_events: bool = Field(
        default=False,
        description=(
            "When true, fetch live roadway events from the requested state "
            "511 clients."
        ),
    )

    state_codes: List[str] = Field(
        default_factory=list,
        description=(
            "Two-letter state codes whose 511 event clients should be called. "
            "Only these states are loaded. Example: ['NV']."
        ),
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
    job_data = redis_client.hgetall(
        _job_key(job_id)
    )

    if not job_data:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    return job_data


def _get_task_results(
    task_ids: List[str],
) -> List[Dict[str, Any]]:
    results = []

    for task_id in task_ids:
        async_result = AsyncResult(
            task_id,
            app=celery_app,
        )

        task_info = {
            "task_id": task_id,
            "status": async_result.status,
            "result": None,
            "error": None,
        }

        if async_result.successful():
            task_info["result"] = async_result.result

        elif async_result.failed():
            task_info["error"] = str(
                async_result.result
            )

        results.append(task_info)

    return results


def _validate_live_weather_segments(
    segments: List[Dict[str, Any]],
) -> None:
    """Ensure every segment has coordinates for live weather."""

    missing_coordinate_labels = []

    for segment in segments:
        if (
            segment.get("latitude") is None
            or segment.get("longitude") is None
        ):
            missing_coordinate_labels.append(
                segment.get(
                    "label",
                    "Unnamed segment",
                )
            )

    if missing_coordinate_labels:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "Live weather mode requires latitude and longitude "
                    "for every segment."
                ),
                "segments_missing_coordinates": missing_coordinate_labels,
            },
        )


def _build_segments_from_routed_checkpoints(
    checkpoints: List[Dict[str, Any]],
    road_condition: str,
    is_night: bool,
    route_id: Optional[str] = None,
    route_label: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Convert routed checkpoints into route-risk segment dictionaries.

    Route identity is included for multi-route comparison jobs.
    """

    segments = []

    for checkpoint in checkpoints:
        segments.append(
            {
                "route_id": route_id,
                "route_label": route_label,
                "label": checkpoint.get(
                    "label",
                    "Route checkpoint",
                ),
                "latitude": checkpoint["latitude"],
                "longitude": checkpoint["longitude"],
                "weather": {
                    "temperature_f": 0,
                    "wind_mph": 0,
                    "condition": (
                        "ignored because live weather is enabled"
                    ),
                    "visibility_miles": None,
                },
                "road_condition": checkpoint.get(
                    "road_condition",
                    road_condition,
                ),
                "road_condition_source": checkpoint.get(
                    "road_condition_source",
                    "fallback",
                ),
                "matched_road_event": checkpoint.get(
                    "matched_road_event"
                ),
                "nearby_road_event_count": checkpoint.get(
                    "nearby_road_event_count",
                    0,
                ),
                "is_night": is_night,
            }
        )

    return segments


def _load_live_state_events(
    use_live_state_events: bool,
    state_codes: List[str],
) -> tuple[
    List[str],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    """
    Load active and upcoming events only for explicitly requested states.

    Active events are returned for scoring. Upcoming events are returned
    separately for informational disclosure and never affect route scores.

    No state API key is read when use_live_state_events is false.
    """

    if not use_live_state_events:
        return [], [], []

    try:
        normalized_state_codes = normalize_state_codes(
            state_codes
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Invalid state code selection.",
                "error": str(exc),
            },
        ) from exc

    if not normalized_state_codes:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "At least one state code is required when "
                    "use_live_state_events is true."
                )
            },
        )

    try:
        state_event_groups = fetch_state_event_groups(
            state_codes=normalized_state_codes,
        )

    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "One or more requested state 511 clients are not "
                    "available."
                ),
                "error": str(exc),
            },
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": (
                    "Failed to retrieve live state roadway events."
                ),
                "error": str(exc),
            },
        ) from exc

    active_state_events = state_event_groups.get(
        "active",
        [],
    )

    upcoming_state_events = state_event_groups.get(
        "upcoming",
        [],
    )

    return (
        normalized_state_codes,
        active_state_events,
        upcoming_state_events,
    )


def _build_upcoming_event_disclosures(
    checkpoints: List[Dict[str, Any]],
    upcoming_events: List[Dict[str, Any]],
    radius_miles: float,
) -> List[Dict[str, Any]]:
    """
    Match upcoming events to route checkpoints without affecting scoring.

    Each event appears once per route and records every sampled checkpoint
    where it was selected as the nearest upcoming event.
    """

    if not upcoming_events:
        return []

    enriched_checkpoints = apply_road_conditions_to_checkpoints(
        checkpoints=checkpoints,
        road_events=upcoming_events,
        radius_miles=radius_miles,
        fallback_road_condition="normal",
    )

    disclosures_by_key: Dict[
        tuple[str, str],
        Dict[str, Any],
    ] = {}

    for checkpoint in enriched_checkpoints:
        matched_event = checkpoint.get(
            "matched_road_event"
        )

        if not matched_event:
            continue

        event_key = (
            str(
                matched_event.get(
                    "source",
                    "",
                )
            ),
            str(
                matched_event.get(
                    "event_id",
                    "",
                )
            ),
        )

        if event_key not in disclosures_by_key:
            disclosures_by_key[event_key] = {
                "event_id": matched_event.get(
                    "event_id"
                ),
                "event_type": matched_event.get(
                    "event_type"
                ),
                "description": matched_event.get(
                    "description"
                ),
                "source": matched_event.get(
                    "source"
                ),
                "state_code": matched_event.get(
                    "state_code"
                ),
                "state_name": matched_event.get(
                    "state_name"
                ),
                "roadway_name": matched_event.get(
                    "roadway_name"
                ),
                "direction_of_travel": matched_event.get(
                    "direction_of_travel"
                ),
                "timing_status": matched_event.get(
                    "timing_status"
                ),
                "start_iso_utc": matched_event.get(
                    "start_iso_utc"
                ),
                "starts_in_hours": matched_event.get(
                    "starts_in_hours"
                ),
                "latitude": matched_event.get(
                    "latitude"
                ),
                "longitude": matched_event.get(
                    "longitude"
                ),
                "checkpoint_labels": [],
            }

        checkpoint_label = checkpoint.get(
            "label",
            "Route checkpoint",
        )

        checkpoint_labels = disclosures_by_key[
            event_key
        ]["checkpoint_labels"]

        if checkpoint_label not in checkpoint_labels:
            checkpoint_labels.append(
                checkpoint_label
            )

    disclosures = list(
        disclosures_by_key.values()
    )

    for disclosure in disclosures:
        disclosure["matched_checkpoint_count"] = len(
            disclosure["checkpoint_labels"]
        )

    return disclosures


def _combine_upcoming_route_disclosures(
    scored_routes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Combine duplicate upcoming-event disclosures across route candidates.
    """

    combined_by_key: Dict[
        tuple[str, str],
        Dict[str, Any],
    ] = {}

    for route in scored_routes:
        route_id = route.get("route_id")
        route_label = route.get("route_label")

        for disclosure in route.get(
            "upcoming_road_events",
            [],
        ):
            event_key = (
                str(
                    disclosure.get(
                        "source",
                        "",
                    )
                ),
                str(
                    disclosure.get(
                        "event_id",
                        "",
                    )
                ),
            )

            if event_key not in combined_by_key:
                combined_disclosure = dict(
                    disclosure
                )

                combined_disclosure.pop(
                    "checkpoint_labels",
                    None,
                )

                combined_disclosure.pop(
                    "matched_checkpoint_count",
                    None,
                )

                combined_disclosure["affected_routes"] = []
                combined_disclosure["route_matches"] = []

                combined_by_key[event_key] = (
                    combined_disclosure
                )

            combined_disclosure = combined_by_key[
                event_key
            ]

            if route_label not in combined_disclosure[
                "affected_routes"
            ]:
                combined_disclosure[
                    "affected_routes"
                ].append(
                    route_label
                )

            combined_disclosure[
                "route_matches"
            ].append(
                {
                    "route_id": route_id,
                    "route_label": route_label,
                    "checkpoint_labels": disclosure.get(
                        "checkpoint_labels",
                        [],
                    ),
                    "matched_checkpoint_count": disclosure.get(
                        "matched_checkpoint_count",
                        0,
                    ),
                }
            )

    return list(
        combined_by_key.values()
    )


# ============================================================
# SINGLE-ROUTE SUMMARY
# ============================================================

def _build_route_risk_summary_response(
    job_id: str,
) -> Dict[str, Any]:
    """Build a clean user-facing single-route summary."""

    job_data = _load_job(job_id)
    workload = job_data.get(
        "workload",
        "unknown",
    )

    if workload != "route_risk_segments":
        raise HTTPException(
            status_code=400,
            detail=(
                "This endpoint only supports route_risk_segments jobs. "
                f"Job workload was: {workload}"
            ),
        )

    task_ids = json.loads(
        job_data["task_ids"]
    )

    task_results = _get_task_results(
        task_ids
    )

    metadata = json.loads(
        job_data.get(
            "metadata",
            "{}",
        )
    )

    aggregated_route_risk = aggregate_job_results(
        task_results
    )

    incomplete_tasks = [
        task_result
        for task_result in task_results
        if task_result.get("status") != "SUCCESS"
    ]

    route_status = (
        "INCOMPLETE"
        if incomplete_tasks
        else "READY"
    )

    blocking_segments = aggregated_route_risk.get(
        "blocking_segments",
        [],
    )

    return {
        "job_id": job_id,
        "route_status": route_status,
        "route_name": metadata.get("route_name"),
        "origin": metadata.get("origin"),
        "destination": metadata.get("destination"),
        "segment_count": metadata.get("segment_count"),
        "coordinate_segment_count": metadata.get(
            "coordinate_segment_count"
        ),
        "weather_mode": metadata.get("weather_mode"),
        "route_source": metadata.get("route_source"),
        "distance_meters": metadata.get("distance_meters"),
        "duration_seconds": metadata.get("duration_seconds"),
        "geometry_point_count": metadata.get(
            "geometry_point_count"
        ),
        "checkpoint_count": metadata.get(
            "checkpoint_count"
        ),
        "road_event_count": metadata.get(
            "road_event_count"
        ),
        "matched_road_event_checkpoint_count": metadata.get(
            "matched_road_event_checkpoint_count"
        ),
        "route_risk_score": aggregated_route_risk[
            "route_risk_score"
        ],
        "route_risk_level": aggregated_route_risk[
            "route_risk_level"
        ],
        "route_blocked": aggregated_route_risk.get(
            "route_blocked",
            False,
        ),
        "route_warning": aggregated_route_risk.get(
            "route_warning"
        ),
        "average_segment_score": aggregated_route_risk.get(
            "average_segment_score"
        ),
        "highest_risk_segment": aggregated_route_risk.get(
            "highest_risk_segment"
        ),
        "blocking_segments": blocking_segments,
        "blocking_segment_count": len(
            blocking_segments
        ),
        "incomplete_task_count": aggregated_route_risk.get(
            "incomplete_task_count",
            0,
        ),
        "summary": aggregated_route_risk["summary"],
    }


# ============================================================
# MULTI-ROUTE COMPARISON
# ============================================================

def _choose_recommended_route(
    scored_routes: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Choose the recommended route.

    Ranking:
    1. Prefer usable routes over blocked routes.
    2. Prefer the lower risk score.
    3. Prefer the shorter duration.
    4. Prefer the shorter distance.
    """

    if not scored_routes:
        return None

    usable_routes = [
        route
        for route in scored_routes
        if not route.get(
            "route_blocked",
            False,
        )
    ]

    candidate_routes = (
        usable_routes
        if usable_routes
        else scored_routes
    )

    return sorted(
        candidate_routes,
        key=lambda route: (
            route.get(
                "route_risk_score",
                999,
            ),
            route.get(
                "duration_seconds",
                999999999,
            ),
            route.get(
                "distance_meters",
                999999999,
            ),
        ),
    )[0]


def _build_route_comparison_summary_response(
    job_id: str,
) -> Dict[str, Any]:
    """Build the distributed multi-route comparison result."""

    job_data = _load_job(job_id)
    workload = job_data.get(
        "workload",
        "unknown",
    )

    if workload != "route_comparison":
        raise HTTPException(
            status_code=400,
            detail=(
                "This endpoint only supports route_comparison jobs. "
                f"Job workload was: {workload}"
            ),
        )

    task_ids = json.loads(
        job_data["task_ids"]
    )

    task_results = _get_task_results(
        task_ids
    )

    task_result_map = {
        task_result["task_id"]: task_result
        for task_result in task_results
    }

    metadata = json.loads(
        job_data.get(
            "metadata",
            "{}",
        )
    )

    scored_routes = []

    for route_metadata in metadata.get(
        "routes",
        [],
    ):
        route_task_results = [
            task_result_map[task_id]
            for task_id in route_metadata.get(
                "task_ids",
                [],
            )
            if task_id in task_result_map
        ]

        aggregated_route_risk = aggregate_job_results(
            route_task_results
        )

        blocking_segments = aggregated_route_risk.get(
            "blocking_segments",
            [],
        )

        incomplete_task_count = aggregated_route_risk.get(
            "incomplete_task_count",
            0,
        )

        route_status = (
            "INCOMPLETE"
            if incomplete_task_count > 0
            else "READY"
        )

        scored_routes.append(
            {
                "route_id": route_metadata["route_id"],
                "route_label": route_metadata.get(
                    "route_label"
                ),
                "route_status": route_status,
                "source": route_metadata.get("source"),
                "provider": route_metadata.get(
                    "provider"
                ),
                "distance_meters": route_metadata.get(
                    "distance_meters"
                ),
                "duration_seconds": route_metadata.get(
                    "duration_seconds"
                ),
                "geometry_point_count": route_metadata.get(
                    "geometry_point_count"
                ),
                "checkpoint_count": route_metadata.get(
                    "checkpoint_count"
                ),
                "matched_road_event_checkpoint_count": route_metadata.get(
                    "matched_road_event_checkpoint_count",
                    0,
                ),
                "matched_upcoming_road_event_checkpoint_count": (
                    route_metadata.get(
                        "matched_upcoming_road_event_checkpoint_count",
                        0,
                    )
                ),
                "upcoming_road_event_count": len(
                    route_metadata.get(
                        "upcoming_road_events",
                        [],
                    )
                ),
                "upcoming_road_events": route_metadata.get(
                    "upcoming_road_events",
                    [],
                ),
                "route_risk_score": aggregated_route_risk.get(
                    "route_risk_score"
                ),
                "route_risk_level": aggregated_route_risk.get(
                    "route_risk_level"
                ),
                "route_blocked": aggregated_route_risk.get(
                    "route_blocked",
                    False,
                ),
                "route_warning": aggregated_route_risk.get(
                    "route_warning"
                ),
                "average_segment_score": aggregated_route_risk.get(
                    "average_segment_score"
                ),
                "highest_risk_segment": aggregated_route_risk.get(
                    "highest_risk_segment"
                ),
                "blocking_segments": blocking_segments,
                "blocking_segment_count": len(
                    blocking_segments
                ),
                "incomplete_task_count": incomplete_task_count,
                "summary": aggregated_route_risk.get(
                    "summary"
                ),
                "aggregated_route_risk": aggregated_route_risk,
            }
        )

    recommended_route = _choose_recommended_route(
        scored_routes
    )

    incomplete_routes = [
        route
        for route in scored_routes
        if route.get("route_status") != "READY"
    ]

    comparison_status = (
        "INCOMPLETE"
        if incomplete_routes
        else "READY"
    )

    upcoming_road_event_disclosures = (
        _combine_upcoming_route_disclosures(
            scored_routes
        )
    )

    return {
        "job_id": job_id,
        "comparison_status": comparison_status,
        "route_name": metadata.get("route_name"),
        "origin": metadata.get("origin"),
        "destination": metadata.get("destination"),
        "route_source": metadata.get(
            "route_source"
        ),
        "route_candidate_count": len(
            scored_routes
        ),
        "checkpoint_count_per_route": metadata.get(
            "checkpoint_count_per_route"
        ),
        "total_checkpoint_task_count": len(
            task_ids
        ),
        "weather_mode": metadata.get(
            "weather_mode"
        ),
        "use_live_state_events": metadata.get(
            "use_live_state_events",
            False,
        ),
        "state_codes": metadata.get(
            "state_codes",
            [],
        ),
        "manual_road_event_count": metadata.get(
            "manual_road_event_count",
            0,
        ),
        "live_state_event_count": metadata.get(
            "live_state_event_count",
            0,
        ),
        "active_state_event_count": metadata.get(
            "active_state_event_count",
            metadata.get(
                "live_state_event_count",
                0,
            ),
        ),
        "upcoming_state_event_count": metadata.get(
            "upcoming_state_event_count",
            0,
        ),
        "road_event_count": metadata.get(
            "road_event_count",
            0,
        ),
        "upcoming_road_event_disclosure_count": len(
            upcoming_road_event_disclosures
        ),
        "upcoming_road_event_disclosures": (
            upcoming_road_event_disclosures
        ),
        "road_event_radius_miles": metadata.get(
            "road_event_radius_miles"
        ),
        "routes": scored_routes,
        "recommended_route": recommended_route,
    }


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
def root() -> Dict[str, str]:
    return {
        "message": "Distributed Route Risk Engine API is running",
        "route_risk_status": (
            "Single-route and multi-route comparison endpoints are available"
        ),
    }


# ============================================================
# ORIGINAL ORCHESTRATOR ENDPOINTS
# ============================================================

@app.post("/submit_batch")
def submit_batch(
    request: NumberBatchRequest,
) -> Dict[str, Any]:
    task_ids = []

    for number in request.numbers:
        task = square_number.delay(
            number
        )
        task_ids.append(
            task.id
        )

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
def submit_slow_batch(
    request: SlowBatchRequest,
) -> Dict[str, Any]:
    task_ids = []

    for number in request.numbers:
        task = slow_square_number.delay(
            number,
            request.delay_seconds,
        )
        task_ids.append(
            task.id
        )

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
def submit_unreliable_batch(
    request: UnreliableBatchRequest,
) -> Dict[str, Any]:
    task_ids = []

    for number in request.numbers:
        task = unreliable_square.delay(
            number,
            request.fail_on_even,
        )
        task_ids.append(
            task.id
        )

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
        task = transient_unreliable_square.delay(
            number,
            request.fail_attempts,
        )
        task_ids.append(
            task.id
        )

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
def submit_matrix_batch(
    request: MatrixBatchRequest,
) -> Dict[str, Any]:
    task_ids = []

    for task_number in range(
        1,
        request.task_count + 1,
    ):
        task = matrix_compute_task.delay(
            task_number,
            request.matrix_size,
        )

        task_ids.append(
            task.id
        )

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
def submit_vector_batch(
    request: VectorBatchRequest,
) -> Dict[str, Any]:
    task_ids = []

    for task_number in range(
        1,
        request.task_count + 1,
    ):
        task = vector_similarity_task.delay(
            task_number,
            request.vector_size,
        )

        task_ids.append(
            task.id
        )

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
# ROUTE RISK ENDPOINTS
# ============================================================

@app.post("/submit_route_risk_job")
def submit_route_risk_job(
    request: RouteRiskJobRequest,
) -> Dict[str, Any]:
    """Submit a route-risk job using provided route segments."""

    task_ids = []

    segments = [
        segment.model_dump()
        for segment in request.segments
    ]

    coordinate_segment_count = sum(
        1
        for segment in segments
        if segment.get("latitude") is not None
        and segment.get("longitude") is not None
    )

    if request.use_live_weather:
        _validate_live_weather_segments(
            segments
        )

    for index, segment in enumerate(
        segments,
        start=1,
    ):
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

        task_ids.append(
            task.id
        )

    weather_mode = (
        "live"
        if request.use_live_weather
        else "manual"
    )

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
            "prototype_stage": (
                f"{weather_mode}_weather_segments"
            ),
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
        "summary_endpoint": (
            f"/route_risk_summary/{job_id}"
        ),
    }


@app.post("/submit_routed_route_risk_job")
def submit_routed_route_risk_job(
    request: RoutedRouteRiskJobRequest,
) -> Dict[str, Any]:
    """Submit a single OSRM routed live-weather route-risk job."""

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
                "message": (
                    "Failed to generate route from routing provider."
                ),
                "error": str(exc),
            },
        ) from exc

    road_events = [
        event.model_dump()
        for event in request.road_events
    ]

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

    for index, segment in enumerate(
        segments,
        start=1,
    ):
        task = live_weather_route_segment_risk_task.delay(
            index,
            segment,
        )

        task_ids.append(
            task.id
        )

    job_id = _create_job(
        workload="route_risk_segments",
        task_ids=task_ids,
        metadata={
            "route_name": request.route_name,
            "origin": request.origin_label,
            "destination": request.destination_label,
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
            "geometry_point_count": route[
                "geometry_point_count"
            ],
            "checkpoint_count": route[
                "checkpoint_count"
            ],
            "fallback_road_condition": request.road_condition,
            "road_event_radius_miles": request.road_event_radius_miles,
            "road_event_count": len(road_events),
            "matched_road_event_checkpoint_count": (
                matched_road_event_checkpoint_count
            ),
            "is_night": request.is_night,
            "prototype_stage": (
                "osrm_routed_live_weather_road_event_segments"
            ),
        },
    )

    return {
        "message": (
            "Routed live-weather route risk job submitted"
        ),
        "job_id": job_id,
        "task_count": len(task_ids),
        "workload": "route_risk_segments",
        "route_name": request.route_name,
        "origin": request.origin_label,
        "destination": request.destination_label,
        "segment_count": len(segments),
        "coordinate_segment_count": len(segments),
        "weather_mode": "live",
        "route_source": route["source"],
        "distance_meters": route["distance_meters"],
        "duration_seconds": route["duration_seconds"],
        "geometry_point_count": route[
            "geometry_point_count"
        ],
        "checkpoint_count": route[
            "checkpoint_count"
        ],
        "road_event_count": len(road_events),
        "matched_road_event_checkpoint_count": (
            matched_road_event_checkpoint_count
        ),
        "summary_endpoint": (
            f"/route_risk_summary/{job_id}"
        ),
    }


@app.post("/submit_route_comparison_job")
def submit_route_comparison_job(
    request: RouteComparisonJobRequest,
) -> Dict[str, Any]:
    """
    Submit a distributed ORS route comparison job.

    Active state events are fetched only for explicitly requested states
    and merged with manual events for scoring. Upcoming events are matched
    separately for disclosure and never affect route scores.
    """

    try:
        route_candidates = fetch_ors_alternative_routes(
            origin_latitude=request.origin_latitude,
            origin_longitude=request.origin_longitude,
            destination_latitude=request.destination_latitude,
            destination_longitude=request.destination_longitude,
            checkpoint_count=request.checkpoint_count,
            target_route_count=request.target_route_count,
            share_factor=request.share_factor,
            weight_factor=request.weight_factor,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": (
                    "Failed to generate alternative routes from "
                    "OpenRouteService."
                ),
                "error": str(exc),
            },
        ) from exc

    manual_road_events = [
        event.model_dump()
        for event in request.road_events
    ]

    (
        normalized_state_codes,
        active_state_events,
        upcoming_state_events,
    ) = _load_live_state_events(
        use_live_state_events=request.use_live_state_events,
        state_codes=request.state_codes,
    )

    combined_road_events = [
        *manual_road_events,
        *active_state_events,
    ]

    routes = route_candidates["routes"]
    all_task_ids = []
    route_metadata_list = []
    global_task_number = 1

    for route in routes:
        enriched_checkpoints = apply_road_conditions_to_checkpoints(
            checkpoints=route["checkpoints"],
            road_events=combined_road_events,
            radius_miles=request.road_event_radius_miles,
            fallback_road_condition=request.road_condition,
        )

        matched_road_event_checkpoint_count = sum(
            1
            for checkpoint in enriched_checkpoints
            if checkpoint.get("matched_road_event") is not None
        )

        upcoming_road_events = _build_upcoming_event_disclosures(
            checkpoints=route["checkpoints"],
            upcoming_events=upcoming_state_events,
            radius_miles=request.road_event_radius_miles,
        )

        matched_upcoming_road_event_checkpoint_count = sum(
            event.get(
                "matched_checkpoint_count",
                0,
            )
            for event in upcoming_road_events
        )

        segments = _build_segments_from_routed_checkpoints(
            checkpoints=enriched_checkpoints,
            road_condition=request.road_condition,
            is_night=request.is_night,
            route_id=route["route_id"],
            route_label=route["route_label"],
        )

        route_task_ids = []

        for segment in segments:
            task = live_weather_route_segment_risk_task.delay(
                global_task_number,
                segment,
            )

            route_task_ids.append(
                task.id
            )

            all_task_ids.append(
                task.id
            )

            global_task_number += 1

        route_metadata_list.append(
            {
                "route_id": route["route_id"],
                "route_label": route["route_label"],
                "source": route["source"],
                "provider": route["provider"],
                "distance_meters": route[
                    "distance_meters"
                ],
                "duration_seconds": route[
                    "duration_seconds"
                ],
                "geometry_point_count": route[
                    "geometry_point_count"
                ],
                "checkpoint_count": route[
                    "checkpoint_count"
                ],
                "matched_road_event_checkpoint_count": (
                    matched_road_event_checkpoint_count
                ),
                "matched_upcoming_road_event_checkpoint_count": (
                    matched_upcoming_road_event_checkpoint_count
                ),
                "upcoming_road_events": upcoming_road_events,
                "task_ids": route_task_ids,
            }
        )

    job_id = _create_job(
        workload="route_comparison",
        task_ids=all_task_ids,
        metadata={
            "route_name": request.route_name,
            "origin": request.origin_label,
            "destination": request.destination_label,
            "origin_latitude": request.origin_latitude,
            "origin_longitude": request.origin_longitude,
            "destination_latitude": request.destination_latitude,
            "destination_longitude": request.destination_longitude,
            "weather_mode": "live",
            "route_source": route_candidates["source"],
            "provider": route_candidates["provider"],
            "route_candidate_count": len(routes),
            "checkpoint_count_per_route": request.checkpoint_count,
            "manual_road_event_count": len(
                manual_road_events
            ),
            "live_state_event_count": len(
                active_state_events
            ),
            "active_state_event_count": len(
                active_state_events
            ),
            "upcoming_state_event_count": len(
                upcoming_state_events
            ),
            "road_event_count": len(
                combined_road_events
            ),
            "road_event_radius_miles": request.road_event_radius_miles,
            "use_live_state_events": request.use_live_state_events,
            "state_codes": normalized_state_codes,
            "fallback_road_condition": request.road_condition,
            "is_night": request.is_night,
            "share_factor": request.share_factor,
            "weight_factor": request.weight_factor,
            "routes": route_metadata_list,
            "prototype_stage": (
                "ors_distributed_multi_route_state_511_comparison"
            ),
        },
    )

    return {
        "message": (
            "Distributed ORS route comparison job submitted"
        ),
        "job_id": job_id,
        "workload": "route_comparison",
        "route_name": request.route_name,
        "origin": request.origin_label,
        "destination": request.destination_label,
        "route_candidate_count": len(routes),
        "checkpoint_count_per_route": request.checkpoint_count,
        "total_checkpoint_task_count": len(
            all_task_ids
        ),
        "weather_mode": "live",
        "route_source": route_candidates["source"],
        "provider": route_candidates["provider"],
        "use_live_state_events": request.use_live_state_events,
        "state_codes": normalized_state_codes,
        "manual_road_event_count": len(
            manual_road_events
        ),
        "live_state_event_count": len(
            active_state_events
        ),
        "active_state_event_count": len(
            active_state_events
        ),
        "upcoming_state_event_count": len(
            upcoming_state_events
        ),
        "road_event_count": len(
            combined_road_events
        ),
        "summary_endpoint": (
            f"/route_comparison_summary/{job_id}"
        ),
    }


@app.get("/route_risk_summary/{job_id}")
def route_risk_summary(
    job_id: str,
) -> Dict[str, Any]:
    return _build_route_risk_summary_response(
        job_id
    )


@app.get("/route_comparison_summary/{job_id}")
def route_comparison_summary(
    job_id: str,
) -> Dict[str, Any]:
    return _build_route_comparison_summary_response(
        job_id
    )


# ============================================================
# SHARED STATUS / RESULTS ENDPOINTS
# ============================================================

@app.get("/job_status/{job_id}")
def job_status(
    job_id: str,
) -> Dict[str, Any]:
    job_data = _load_job(
        job_id
    )

    task_ids = json.loads(
        job_data["task_ids"]
    )

    total_tasks = int(
        job_data["total_tasks"]
    )

    completed_tasks = 0
    failed_tasks = 0
    pending_tasks = 0
    running_tasks = 0
    retrying_tasks = 0

    for task_id in task_ids:
        async_result = AsyncResult(
            task_id,
            app=celery_app,
        )

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

    unfinished_tasks = (
        pending_tasks
        + running_tasks
        + retrying_tasks
    )

    finished_tasks = (
        completed_tasks
        + failed_tasks
    )

    if total_tasks > 0:
        progress_percent = round(
            (finished_tasks / total_tasks) * 100,
            2,
        )

    else:
        progress_percent = 0.0

    if (
        failed_tasks > 0
        and finished_tasks == total_tasks
    ):
        status = "PARTIAL_FAILURE"

    elif completed_tasks == total_tasks:
        status = "SUCCESS"

    elif (
        unfinished_tasks > 0
        or finished_tasks > 0
    ):
        status = "IN_PROGRESS"

    else:
        status = "PENDING"

    return {
        "job_id": job_id,
        "workload": job_data.get(
            "workload",
            "unknown",
        ),
        "status": status,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "failed_tasks": failed_tasks,
        "pending_tasks": pending_tasks,
        "running_tasks": running_tasks,
        "retrying_tasks": retrying_tasks,
        "progress_percent": progress_percent,
        "metadata": json.loads(
            job_data.get(
                "metadata",
                "{}",
            )
        ),
    }


@app.get("/results/{job_id}")
def results(
    job_id: str,
) -> Dict[str, Any]:
    job_data = _load_job(
        job_id
    )

    task_ids = json.loads(
        job_data["task_ids"]
    )

    task_results = _get_task_results(
        task_ids
    )

    workload = job_data.get(
        "workload",
        "unknown",
    )

    response = {
        "job_id": job_id,
        "workload": workload,
        "metadata": json.loads(
            job_data.get(
                "metadata",
                "{}",
            )
        ),
        "results": task_results,
    }

    if workload == "route_risk_segments":
        response["aggregated_route_risk"] = (
            aggregate_job_results(
                task_results
            )
        )

    if workload == "route_comparison":
        response["route_comparison"] = (
            _build_route_comparison_summary_response(
                job_id
            )
        )

    return response