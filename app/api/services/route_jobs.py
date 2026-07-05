from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.api.job_store import create_job
from app.api.models import (
    RouteComparisonJobRequest,
    RoutedRouteRiskJobRequest,
)
from app.worker.tasks import live_weather_route_segment_risk_task
from route_risk.integrations.ors_client import (
    fetch_ors_alternative_routes,
)
from route_risk.integrations.road_conditions_client import (
    apply_road_conditions_to_checkpoints,
)
from route_risk.integrations.road_conditions_client import (
    filter_road_events_for_route,
)
from route_risk.integrations.routing_client import (
    fetch_route_between_coordinates,
)
from route_risk.integrations.state_511_clients.state_event_loader import (
    fetch_state_event_groups,
    normalize_state_codes,
)
from route_risk.core.route_similarity import (
    filter_near_duplicate_routes,
)
from route_risk.core.driving_period import (
    apply_driving_period_to_events,
)


EventList = List[Dict[str, Any]]


# Simplified state-border polygons used only to decide which supported 511
# providers should be loaded. Actual event matching still uses route and
# event geometry later in the pipeline.
SUPPORTED_STATE_POLYGONS: Dict[
    str,
    List[Tuple[float, float]],
] = {
    "NV": [
        (42.05, -120.10),
        (42.05, -113.99),
        (36.95, -113.99),
        (35.90, -114.55),
        (34.95, -114.70),
        (38.95, -120.10),
    ],
    "AZ": [
        (37.05, -114.10),
        (37.05, -108.99),
        (31.28, -108.99),
        (31.28, -114.88),
        (32.75, -114.80),
        (34.95, -114.72),
        (36.10, -114.85),
    ],
    "UT": [
        (42.05, -114.10),
        (42.05, -108.99),
        (36.95, -108.99),
        (36.95, -114.10),
    ],
}


def _extract_latitude_longitude(
    point: Any,
) -> Optional[Tuple[float, float]]:
    """
    Read a route point from the formats currently used by the project.

    Dictionary points use latitude/longitude keys. Coordinate pairs are
    treated as GeoJSON-style longitude, latitude values.
    """

    if isinstance(point, dict):
        latitude = point.get("latitude")
        longitude = point.get("longitude")

    elif (
        isinstance(point, (list, tuple))
        and len(point) >= 2
    ):
        longitude = point[0]
        latitude = point[1]

    else:
        return None

    try:
        latitude = float(latitude)
        longitude = float(longitude)

    except (TypeError, ValueError):
        return None

    if not (
        -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        return None

    return latitude, longitude


def _point_is_inside_polygon(
    latitude: float,
    longitude: float,
    polygon: List[Tuple[float, float]],
) -> bool:
    """
    Return whether a latitude/longitude point is inside a polygon.
    """

    inside = False
    previous_index = len(polygon) - 1

    for current_index, current_point in enumerate(
        polygon
    ):
        current_latitude, current_longitude = (
            current_point
        )

        previous_latitude, previous_longitude = (
            polygon[previous_index]
        )

        crosses_latitude = (
            (current_latitude > latitude)
            != (previous_latitude > latitude)
        )

        if crosses_latitude:
            intersection_longitude = (
                (
                    previous_longitude
                    - current_longitude
                )
                * (
                    latitude
                    - current_latitude
                )
                / (
                    previous_latitude
                    - current_latitude
                )
                + current_longitude
            )

            if longitude <= intersection_longitude:
                inside = not inside

        previous_index = current_index

    return inside


def _detect_supported_state_codes(
    routes: EventList,
) -> List[str]:
    """
    Detect supported state feeds touched by generated route geometry.

    Full route geometry is preferred. Checkpoints are used as a fallback.
    """

    detected_state_codes: List[str] = []

    for route in routes:
        route_points = route.get(
            "geometry_coordinates",
            [],
        )

        if not route_points:
            route_points = route.get(
                "checkpoints",
                [],
            )

        for point in route_points:
            coordinate = (
                _extract_latitude_longitude(
                    point
                )
            )

            if coordinate is None:
                continue

            latitude, longitude = coordinate

            for state_code, polygon in (
                SUPPORTED_STATE_POLYGONS.items()
            ):
                if (
                    state_code
                    not in detected_state_codes
                    and _point_is_inside_polygon(
                        latitude=latitude,
                        longitude=longitude,
                        polygon=polygon,
                    )
                ):
                    detected_state_codes.append(
                        state_code
                    )

    return detected_state_codes


def _build_segments_from_routed_checkpoints(
    checkpoints: EventList,
    road_condition: str,
    is_night: bool,
    route_id: Optional[str] = None,
    route_label: Optional[str] = None,
) -> EventList:
    """
    Convert route checkpoints into Celery route-risk task inputs.
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
) -> Tuple[List[str], EventList, EventList]:
    """
    Load active and upcoming events for selected supported states.

    Active events may affect scoring. Upcoming events are disclosures only.
    An empty state list means no supported route states were detected.
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
        return [], [], []

    try:
        event_groups = fetch_state_event_groups(
            state_codes=normalized_state_codes,
        )

    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "One or more requested state 511 clients "
                    "are not available."
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

    return (
        normalized_state_codes,
        event_groups.get("active", []),
        event_groups.get("upcoming", []),
    )


def _build_upcoming_event_disclosures(
    checkpoints: EventList,
    upcoming_events: EventList,
    radius_miles: float,
) -> EventList:
    """
    Match upcoming events to checkpoints without affecting route scoring.
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
        Tuple[str, str],
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
        disclosure[
            "matched_checkpoint_count"
        ] = len(
            disclosure["checkpoint_labels"]
        )

    return disclosures


def submit_single_route_job(
    request: RoutedRouteRiskJobRequest,
) -> Dict[str, Any]:
    """
    Generate and submit one OSRM route for distributed risk analysis.
    """

    try:
        route = fetch_route_between_coordinates(
            origin_latitude=(
                request.origin_latitude
            ),
            origin_longitude=(
                request.origin_longitude
            ),
            destination_latitude=(
                request.destination_latitude
            ),
            destination_longitude=(
                request.destination_longitude
            ),
            checkpoint_count=(
                request.checkpoint_count
            ),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": (
                    "Failed to generate route "
                    "from routing provider."
                ),
                "error": str(exc),
            },
        ) from exc

    road_events = [
        event.model_dump()
        for event in request.road_events
    ]

    enriched_checkpoints = (
        apply_road_conditions_to_checkpoints(
            checkpoints=route[
                "checkpoints"
            ],
            road_events=road_events,
            radius_miles=(
                request.road_event_radius_miles
            ),
            fallback_road_condition=(
                request.road_condition
            ),
        )
    )

    matched_checkpoint_count = sum(
        1
        for checkpoint
        in enriched_checkpoints
        if checkpoint.get(
            "matched_road_event"
        ) is not None
    )

    segments = (
        _build_segments_from_routed_checkpoints(
            checkpoints=enriched_checkpoints,
            road_condition=(
                request.road_condition
            ),
            is_night=request.is_night,
        )
    )

    task_ids = []

    for index, segment in enumerate(
        segments,
        start=1,
    ):
        task = (
            live_weather_route_segment_risk_task.delay(
                index,
                segment,
            )
        )

        task_ids.append(task.id)

    metadata = {
        "route_name": request.route_name,
        "origin": request.origin_label,
        "destination": (
            request.destination_label
        ),
        "origin_latitude": (
            request.origin_latitude
        ),
        "origin_longitude": (
            request.origin_longitude
        ),
        "destination_latitude": (
            request.destination_latitude
        ),
        "destination_longitude": (
            request.destination_longitude
        ),
        "segment_count": len(segments),
        "coordinate_segment_count": len(
            segments
        ),
        "weather_mode": "live",
        "route_source": route["source"],
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
        "fallback_road_condition": (
            request.road_condition
        ),
        "road_event_radius_miles": (
            request.road_event_radius_miles
        ),
        "road_event_count": len(
            road_events
        ),
        "matched_road_event_checkpoint_count": (
            matched_checkpoint_count
        ),
        "is_night": request.is_night,
        "prototype_stage": (
            "osrm_routed_live_weather_"
            "road_event_segments"
        ),
    }

    job_id = create_job(
        workload="route_risk_segments",
        task_ids=task_ids,
        metadata=metadata,
    )

    return {
        "message": (
            "Routed live-weather route "
            "risk job submitted"
        ),
        "job_id": job_id,
        "task_count": len(task_ids),
        "workload": "route_risk_segments",
        "route_name": request.route_name,
        "origin": request.origin_label,
        "destination": (
            request.destination_label
        ),
        "segment_count": len(segments),
        "coordinate_segment_count": len(
            segments
        ),
        "weather_mode": "live",
        "route_source": route["source"],
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
        "road_event_count": len(
            road_events
        ),
        "matched_road_event_checkpoint_count": (
            matched_checkpoint_count
        ),
        "summary_endpoint": (
            f"/route_risk_summary/{job_id}"
        ),
    }


def submit_comparison_job(
    request: RouteComparisonJobRequest,
) -> Dict[str, Any]:
    """
    Generate ORS alternatives and submit their checkpoints to Celery.
    """

    try:
        route_candidates = (
            fetch_ors_alternative_routes(
                origin_latitude=(
                    request.origin_latitude
                ),
                origin_longitude=(
                    request.origin_longitude
                ),
                destination_latitude=(
                    request.destination_latitude
                ),
                destination_longitude=(
                    request.destination_longitude
                ),
                checkpoint_count=(
                    request.checkpoint_count
                ),
                target_route_count=(
                    request.target_route_count
                ),
                share_factor=(
                    request.share_factor
                ),
                weight_factor=(
                    request.weight_factor
                ),
            )
        )

    except Exception as exc:
        error_text = str(exc)

        alternative_route_limit_reached = any(
            marker in error_text
            for marker in (
                '"code":2004',
                '"code": 2004',
                "alternative Routes algorithm",
                "alternative routes algorithm",
                "must not be greater than 100000.0 meters",
            )
        )

        if alternative_route_limit_reached:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": (
                        "ROUTE_TOO_LONG_FOR_ALTERNATIVE_ROUTES"
                    ),
                    "message": (
                        "This route is too long for the "
                        "three-route comparison demo. "
                        "Alternative routes are currently limited "
                        "to about 62 miles (100 km). Please choose "
                        "closer locations."
                    ),
                    "maximum_distance_meters": 100000,
                    "maximum_distance_miles": 62.1,
                },
            ) from exc

        raise HTTPException(
            status_code=502,
            detail={
                "code": "ROUTE_PROVIDER_ERROR",
                "message": (
                    "OpenRouteService could not generate "
                    "the requested routes."
                ),
                "error": error_text,
            },
        ) from exc

    manual_road_events = [
        event.model_dump()
        for event in request.road_events
    ]

    generated_routes = route_candidates[
        "routes"
    ]

    route_duplicate_filter = (
        filter_near_duplicate_routes(
            generated_routes,
            similarity_threshold=0.94,
            proximity_tolerance_miles=0.10,
            maximum_sample_points=300,
        )
    )

    routes = route_duplicate_filter[
        "routes"
    ]

    selected_state_codes: List[str]

    if not request.use_live_state_events:
        state_selection_mode = "disabled"
        selected_state_codes = []

    elif request.state_codes:
        state_selection_mode = (
            "manual_override"
        )

        selected_state_codes = (
            request.state_codes
        )

    else:
        state_selection_mode = (
            "automatic_route_geometry"
        )

        selected_state_codes = (
            _detect_supported_state_codes(
                routes
            )
        )

    (
        normalized_state_codes,
        active_state_events,
        upcoming_state_events,
    ) = _load_live_state_events(
        use_live_state_events=(
            request.use_live_state_events
        ),
        state_codes=selected_state_codes,
    )

    scoring_events = [
        *manual_road_events,
        *active_state_events,
    ]

    all_task_ids = []
    route_metadata_list = []
    global_task_number = 1

    scoring_events = (
        apply_driving_period_to_events(
            scoring_events,
            is_night=request.is_night,
        )
    )

    for route in routes:
        route_scoring_events = (
            filter_road_events_for_route(
                route_geometry=route.get(
                    "geometry_coordinates",
                    [],
                ),
                road_events=scoring_events,
                radius_miles=(
                    request.road_event_radius_miles
                ),
            )
        )

        enriched_checkpoints = (
            apply_road_conditions_to_checkpoints(
                checkpoints=route[
                    "checkpoints"
                ],
                road_events=(
                    route_scoring_events
                ),
                radius_miles=(
                    request.road_event_radius_miles
                ),
                fallback_road_condition=(
                    request.road_condition
                ),
            )
        )

        matched_checkpoint_count = sum(
            1
            for checkpoint
            in enriched_checkpoints
            if checkpoint.get(
                "matched_road_event"
            ) is not None
        )

        upcoming_disclosures = (
            _build_upcoming_event_disclosures(
                checkpoints=route[
                    "checkpoints"
                ],
                upcoming_events=(
                    upcoming_state_events
                ),
                radius_miles=(
                    request.road_event_radius_miles
                ),
            )
        )

        matched_upcoming_checkpoint_count = sum(
            event.get(
                "matched_checkpoint_count",
                0,
            )
            for event in upcoming_disclosures
        )

        segments = (
            _build_segments_from_routed_checkpoints(
                checkpoints=(
                    enriched_checkpoints
                ),
                road_condition=(
                    request.road_condition
                ),
                is_night=request.is_night,
                route_id=route[
                    "route_id"
                ],
                route_label=route[
                    "route_label"
                ],
            )
        )

        route_task_ids = []

        for segment in segments:
            task = (
                live_weather_route_segment_risk_task.delay(
                    global_task_number,
                    segment,
                )
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
                "route_id": route[
                    "route_id"
                ],
                "route_label": route[
                    "route_label"
                ],
                "source": route[
                    "source"
                ],
                "provider": route[
                    "provider"
                ],
                "distance_meters": route[
                    "distance_meters"
                ],
                "duration_seconds": route[
                    "duration_seconds"
                ],
                "geometry_point_count": route[
                    "geometry_point_count"
                ],
                "geometry_coordinates": (
                    route.get(
                        "geometry_coordinates",
                        [],
                    )
                ),
                "checkpoint_count": route[
                    "checkpoint_count"
                ],
                "matched_road_event_checkpoint_count": (
                    matched_checkpoint_count
                ),
                "matched_upcoming_road_event_checkpoint_count": (
                    matched_upcoming_checkpoint_count
                ),
                "upcoming_road_events": (
                    upcoming_disclosures
                ),
                "task_ids": route_task_ids,
            }
        )

    metadata = {
        "route_name": request.route_name,
        "origin": request.origin_label,
        "destination": (
            request.destination_label
        ),
        "origin_latitude": (
            request.origin_latitude
        ),
        "origin_longitude": (
            request.origin_longitude
        ),
        "destination_latitude": (
            request.destination_latitude
        ),
        "destination_longitude": (
            request.destination_longitude
        ),
        "weather_mode": "live",
        "route_source": route_candidates[
            "source"
        ],
        "provider": route_candidates[
            "provider"
        ],
        "route_candidate_count": len(
            routes
        ),
        "generated_route_candidate_count": (
            route_duplicate_filter[
                "generated_count"
            ]
        ),
        "unique_route_candidate_count": (
            route_duplicate_filter[
                "unique_count"
            ]
        ),
        "duplicate_route_count": (
            route_duplicate_filter[
                "duplicate_count"
            ]
        ),
        "duplicate_routes": (
            route_duplicate_filter[
                "duplicate_routes"
            ]
        ),
        "route_similarity_threshold": (
            route_duplicate_filter[
                "similarity_threshold"
            ]
        ),
        "route_similarity_tolerance_miles": (
            route_duplicate_filter[
                "proximity_tolerance_miles"
            ]
        ),
        "checkpoint_count_per_route": (
            request.checkpoint_count
        ),
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
            scoring_events
        ),
        "road_event_radius_miles": (
            request.road_event_radius_miles
        ),
        "use_live_state_events": (
            request.use_live_state_events
        ),
        "state_codes": (
            normalized_state_codes
        ),
        "state_selection_mode": (
            state_selection_mode
        ),
        "fallback_road_condition": (
            request.road_condition
        ),
        "is_night": request.is_night,
        "share_factor": (
            request.share_factor
        ),
        "weight_factor": (
            request.weight_factor
        ),
        "routes": route_metadata_list,
        "prototype_stage": (
            "ors_distributed_multi_route_"
            "state_511_comparison"
        ),
    }

    job_id = create_job(
        workload="route_comparison",
        task_ids=all_task_ids,
        metadata=metadata,
    )

    return {
        "message": (
            "Distributed ORS route "
            "comparison job submitted"
        ),
        "job_id": job_id,
        "workload": "route_comparison",
        "route_name": request.route_name,
        "origin": request.origin_label,
        "destination": (
            request.destination_label
        ),
        "route_candidate_count": len(
            routes
        ),
        "generated_route_candidate_count": (
            route_duplicate_filter[
                "generated_count"
            ]
        ),
        "unique_route_candidate_count": (
            route_duplicate_filter[
                "unique_count"
            ]
        ),
        "duplicate_route_count": (
            route_duplicate_filter[
                "duplicate_count"
            ]
        ),
        "duplicate_routes": (
            route_duplicate_filter[
                "duplicate_routes"
            ]
        ),
        "checkpoint_count_per_route": (
            request.checkpoint_count
        ),
        "total_checkpoint_task_count": len(
            all_task_ids
        ),
        "weather_mode": "live",
        "route_source": route_candidates[
            "source"
        ],
        "provider": route_candidates[
            "provider"
        ],
        "use_live_state_events": (
            request.use_live_state_events
        ),
        "state_codes": (
            normalized_state_codes
        ),
        "state_selection_mode": (
            state_selection_mode
        ),
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
            scoring_events
        ),
        "summary_endpoint": (
            f"/route_comparison_summary/"
            f"{job_id}"
        ),
    }