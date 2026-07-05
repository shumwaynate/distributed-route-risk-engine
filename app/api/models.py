from typing import List

from pydantic import BaseModel, Field


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
            "Optional two-letter state codes whose 511 clients should be "
            "called. When empty and live state events are enabled, supported "
            "states are detected automatically from the generated route "
            "geometry. Explicit codes act as a manual override."
        ),
    )