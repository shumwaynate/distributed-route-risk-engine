"""
route_risk/integrations/state_511_clients/arizona_511_client.py

Arizona 511 traffic-event integration.

Purpose:
- Fetch current Arizona traffic events.
- Normalize Arizona-specific data into the shared road-event structure.
- Separate active, upcoming, future, expired, and unknown-time events.
- Return only active events for route scoring.
- Preserve upcoming events for later informational route summaries.

Run manually from the project root:

    python -m route_risk.integrations.state_511_clients.arizona_511_client
"""

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from route_risk.config import get_arizona_511_api_key


ARIZONA_511_EVENTS_URL = (
    "https://az511.com/api/v2/get/event"
)

DEFAULT_UPCOMING_WINDOW_HOURS = 48.0


def _coerce_arizona_boolean(
    value: Any,
) -> bool:
    """
    Convert Arizona API boolean-like values into an actual bool.
    """

    if isinstance(value, bool):
        return value

    if value is None:
        return False

    if isinstance(value, (int, float)):
        return value != 0

    return str(value).strip().lower() in {
        "true",
        "1",
        "yes",
        "y",
    }


def classify_arizona_closure_scope(
    event_type: Any,
    event_subtype: Any = None,
    is_full_closure: bool = False,
    lanes_affected: Any = None,
    description: Any = None,
    details: Any = None,
) -> str:
    """
    Describe the scope of an Arizona closure.

    Returned values:
    - full
    - ramp
    - shoulder
    - partial_lane
    - partial_unspecified
    - none

    Only ``full`` is treated as route blocking.
    """

    if is_full_closure:
        return "full"

    combined_text = " ".join(
        str(value or "").strip().lower()
        for value in (
            event_type,
            event_subtype,
            lanes_affected,
            description,
            details,
        )
    )

    closure_language_present = any(
        phrase in combined_text
        for phrase in (
            "closed",
            "closure",
            "closures",
        )
    )

    if not closure_language_present:
        return "none"

    if "shoulder" in combined_text:
        return "shoulder"

    if "ramp" in combined_text:
        return "ramp"

    partial_lane_phrases = (
        "1 lane",
        "one lane",
        "single lane",
        "left lane",
        "right lane",
        "lane reduced",
        "lanes reduced",
        "reduced lanes",
        "lane affected",
        "lanes affected",
        "flagging",
    )

    if any(
        phrase in combined_text
        for phrase in partial_lane_phrases
    ):
        return "partial_lane"

    return "partial_unspecified"


def normalize_arizona_event_type(
    event_type: Any,
    event_subtype: Any = None,
    is_full_closure: bool = False,
    lanes_affected: Any = None,
    description: Any = None,
    details: Any = None,
) -> str:
    """
    Convert Arizona 511 event values into shared event names.

    Blocking behavior:
    - IsFullClosure == True -> road closure
    - Non-full lane, ramp, or shoulder closures -> construction
    """

    normalized_type = str(
        event_type or ""
    ).strip().lower()

    normalized_subtype = str(
        event_subtype or ""
    ).strip().lower()

    if is_full_closure:
        return "road closure"

    closure_scope = classify_arizona_closure_scope(
        event_type=event_type,
        event_subtype=event_subtype,
        is_full_closure=is_full_closure,
        lanes_affected=lanes_affected,
        description=description,
        details=details,
    )

    if (
        normalized_type == "closures"
        or "closure" in normalized_type
        or "closure" in normalized_subtype
        or closure_scope != "none"
    ):
        return "construction"

    if normalized_type == "roadwork":
        return "construction"

    if (
        "construction" in normalized_type
        or "construction" in normalized_subtype
        or "maintenance" in normalized_type
        or "maintenance" in normalized_subtype
        or "road work" in normalized_type
        or "road work" in normalized_subtype
        or "roadwork" in normalized_type
        or "roadwork" in normalized_subtype
        or "lane marking" in normalized_subtype
        or "widening" in normalized_subtype
    ):
        return "construction"

    if normalized_type == "accidentsandincidents":
        return "incident"

    if (
        "accident" in normalized_type
        or "accident" in normalized_subtype
        or "crash" in normalized_type
        or "crash" in normalized_subtype
        or "incident" in normalized_type
        or "incident" in normalized_subtype
    ):
        return "incident"

    return "caution"


def _parse_arizona_timestamp(
    value: Any,
) -> Optional[float]:
    """
    Convert an Arizona timestamp value into Unix seconds.

    Handles both second and millisecond timestamps.
    """

    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return None

    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None

    if timestamp <= 0:
        return None

    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000.0

    return timestamp


def _timestamp_to_iso_utc(
    timestamp: Optional[float],
) -> Optional[str]:
    """
    Convert a Unix timestamp into an ISO-8601 UTC string.
    """

    if timestamp is None:
        return None

    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).isoformat()


def classify_arizona_event_timing(
    start_date: Any,
    planned_end_date: Any,
    reference_timestamp: Optional[float] = None,
    upcoming_window_hours: float = DEFAULT_UPCOMING_WINDOW_HOURS,
) -> Dict[str, Any]:
    """
    Classify an Arizona event according to its start and end times.

    Status values:
    - active
    - upcoming
    - future
    - expired
    - unknown
    """

    current_timestamp = (
        float(reference_timestamp)
        if reference_timestamp is not None
        else time.time()
    )

    start_timestamp = _parse_arizona_timestamp(
        start_date
    )

    end_timestamp = _parse_arizona_timestamp(
        planned_end_date
    )

    upcoming_window_seconds = (
        float(upcoming_window_hours)
        * 60
        * 60
    )

    if (
        end_timestamp is not None
        and end_timestamp < current_timestamp
    ):
        timing_status = "expired"

    elif (
        start_timestamp is not None
        and start_timestamp > current_timestamp
    ):
        seconds_until_start = (
            start_timestamp
            - current_timestamp
        )

        if (
            seconds_until_start
            <= upcoming_window_seconds
        ):
            timing_status = "upcoming"
        else:
            timing_status = "future"

    elif (
        start_timestamp is None
        and end_timestamp is None
    ):
        timing_status = "unknown"

    else:
        timing_status = "active"

    if start_timestamp is not None:
        starts_in_hours = round(
            (
                start_timestamp
                - current_timestamp
            )
            / 3600,
            2,
        )
    else:
        starts_in_hours = None

    if end_timestamp is not None:
        ends_in_hours = round(
            (
                end_timestamp
                - current_timestamp
            )
            / 3600,
            2,
        )
    else:
        ends_in_hours = None

    return {
        "timing_status": timing_status,
        "start_timestamp": start_timestamp,
        "planned_end_timestamp": end_timestamp,
        "start_iso_utc": _timestamp_to_iso_utc(
            start_timestamp
        ),
        "planned_end_iso_utc": _timestamp_to_iso_utc(
            end_timestamp
        ),
        "starts_in_hours": starts_in_hours,
        "ends_in_hours": ends_in_hours,
    }


def normalize_arizona_511_event(
    raw_event: Dict[str, Any],
    reference_timestamp: Optional[float] = None,
    upcoming_window_hours: float = DEFAULT_UPCOMING_WINDOW_HOURS,
) -> Dict[str, Any]:
    """
    Normalize one Arizona 511 event into the shared format.
    """

    event_id = raw_event.get("ID")
    event_type = raw_event.get("EventType")
    event_subtype = raw_event.get("EventSubType")
    lanes_affected = raw_event.get(
        "LanesAffected"
    )

    is_full_closure = _coerce_arizona_boolean(
        raw_event.get(
            "IsFullClosure",
            False,
        )
    )

    description = str(
        raw_event.get("Description") or ""
    ).strip()

    details = str(
        raw_event.get("Details") or ""
    ).strip()

    closure_scope = classify_arizona_closure_scope(
        event_type=event_type,
        event_subtype=event_subtype,
        is_full_closure=is_full_closure,
        lanes_affected=lanes_affected,
        description=description,
        details=details,
    )

    is_blocking_closure = (
        is_full_closure
        and closure_scope == "full"
    )

    normalized_event_type = (
        normalize_arizona_event_type(
            event_type=event_type,
            event_subtype=event_subtype,
            is_full_closure=is_full_closure,
            lanes_affected=lanes_affected,
            description=description,
            details=details,
        )
    )

    timing = classify_arizona_event_timing(
        start_date=raw_event.get(
            "StartDate"
        ),
        planned_end_date=raw_event.get(
            "PlannedEndDate"
        ),
        reference_timestamp=reference_timestamp,
        upcoming_window_hours=upcoming_window_hours,
    )

    if not description and details:
        description = details

    if not description:
        description = (
            f"Arizona 511 "
            f"{normalized_event_type} event"
        )

    return {
        "event_id": (
            f"arizona-511-{event_id}"
        ),
        "event_type": normalized_event_type,
        "description": description,
        "latitude": raw_event.get(
            "Latitude"
        ),
        "longitude": raw_event.get(
            "Longitude"
        ),
        "source": "arizona-511-events",

        "timing_status": timing[
            "timing_status"
        ],
        "start_timestamp": timing[
            "start_timestamp"
        ],
        "planned_end_timestamp": timing[
            "planned_end_timestamp"
        ],
        "start_iso_utc": timing[
            "start_iso_utc"
        ],
        "planned_end_iso_utc": timing[
            "planned_end_iso_utc"
        ],
        "starts_in_hours": timing[
            "starts_in_hours"
        ],
        "ends_in_hours": timing[
            "ends_in_hours"
        ],

        "is_full_closure": (
            is_full_closure
        ),
        "is_blocking_closure": (
            is_blocking_closure
        ),
        "closure_scope": closure_scope,

        "source_event_id": event_id,
        "source_id": raw_event.get(
            "SourceId"
        ),
        "organization": raw_event.get(
            "Organization"
        ),
        "roadway_name": raw_event.get(
            "RoadwayName"
        ),
        "direction_of_travel": (
            raw_event.get(
                "DirectionOfTravel"
            )
        ),
        "event_subtype": event_subtype,
        "severity": raw_event.get(
            "Severity"
        ),
        "lane_count": raw_event.get(
            "LaneCount"
        ),
        "lanes_affected": (
            lanes_affected
        ),
        "reported_unix": raw_event.get(
            "Reported"
        ),
        "last_updated_unix": (
            raw_event.get(
                "LastUpdated"
            )
        ),
        "recurrence": raw_event.get(
            "Recurrence"
        ),
        "recurrence_schedules": (
            raw_event.get(
                "RecurrenceSchedules"
            )
        ),
        "latitude_secondary": (
            raw_event.get(
                "LatitudeSecondary"
            )
        ),
        "longitude_secondary": (
            raw_event.get(
                "LongitudeSecondary"
            )
        ),
        "encoded_polyline": (
            raw_event.get(
                "EncodedPolyline"
            )
        ),
        "detour_polyline": (
            raw_event.get(
                "DetourPolyline"
            )
        ),
        "detour_instructions": (
            raw_event.get(
                "DetourInstructions"
            )
        ),
        "restrictions": raw_event.get(
            "Restrictions"
        ),
        "details": details,
    }


def fetch_raw_arizona_511_events(
    timeout_seconds: float = 20.0,
) -> List[Dict[str, Any]]:
    """
    Fetch the current raw traffic-event list from Arizona 511.
    """

    api_key = (
        get_arizona_511_api_key()
    )

    response = requests.get(
        ARIZONA_511_EVENTS_URL,
        params={
            "key": api_key,
            "format": "json",
        },
        timeout=timeout_seconds,
    )

    response.raise_for_status()

    response_data = response.json()

    if not isinstance(
        response_data,
        list,
    ):
        raise RuntimeError(
            "Arizona 511 returned an "
            "unexpected response. "
            "Expected a JSON list of "
            "traffic events."
        )

    return response_data


def fetch_arizona_511_event_groups(
    timeout_seconds: float = 20.0,
    upcoming_window_hours: float = DEFAULT_UPCOMING_WINDOW_HOURS,
    reference_timestamp: Optional[float] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch, normalize, and group Arizona events by timing status.

    The API is called only once.
    """

    raw_events = (
        fetch_raw_arizona_511_events(
            timeout_seconds=timeout_seconds
        )
    )

    groups: Dict[
        str,
        List[Dict[str, Any]],
    ] = {
        "active": [],
        "upcoming": [],
        "future": [],
        "expired": [],
        "unknown": [],
    }

    for raw_event in raw_events:
        normalized_event = (
            normalize_arizona_511_event(
                raw_event=raw_event,
                reference_timestamp=(
                    reference_timestamp
                ),
                upcoming_window_hours=(
                    upcoming_window_hours
                ),
            )
        )

        latitude = normalized_event.get(
            "latitude"
        )

        longitude = normalized_event.get(
            "longitude"
        )

        if (
            latitude is None
            or longitude is None
        ):
            continue

        timing_status = (
            normalized_event.get(
                "timing_status",
                "unknown",
            )
        )

        if timing_status not in groups:
            timing_status = "unknown"

        groups[timing_status].append(
            normalized_event
        )

    return groups


def fetch_arizona_511_events(
    timeout_seconds: float = 20.0,
) -> List[Dict[str, Any]]:
    """
    Fetch active Arizona events for route scoring.
    """

    groups = (
        fetch_arizona_511_event_groups(
            timeout_seconds=timeout_seconds
        )
    )

    return groups["active"]


def fetch_arizona_511_upcoming_events(
    timeout_seconds: float = 20.0,
    upcoming_window_hours: float = DEFAULT_UPCOMING_WINDOW_HOURS,
) -> List[Dict[str, Any]]:
    """
    Fetch Arizona events beginning within the upcoming window.
    """

    groups = (
        fetch_arizona_511_event_groups(
            timeout_seconds=timeout_seconds,
            upcoming_window_hours=(
                upcoming_window_hours
            ),
        )
    )

    return groups["upcoming"]


def print_section_title(
    title: str,
) -> None:
    """
    Print a readable terminal section heading.
    """

    print(
        "\n============================================================"
    )
    print(title)
    print(
        "============================================================\n"
    )


if __name__ == "__main__":
    print_section_title(
        "ARIZONA 511 EVENT TIMING TEST"
    )

    event_groups = (
        fetch_arizona_511_event_groups()
    )

    print(
        f"Active events: "
        f"{len(event_groups['active'])}"
    )

    print(
        f"Upcoming events within "
        f"{DEFAULT_UPCOMING_WINDOW_HOURS:.0f} hours: "
        f"{len(event_groups['upcoming'])}"
    )

    print(
        f"Future events: "
        f"{len(event_groups['future'])}"
    )

    print(
        f"Expired events: "
        f"{len(event_groups['expired'])}"
    )

    print(
        f"Unknown-time events: "
        f"{len(event_groups['unknown'])}"
    )

    print_section_title(
        "FIRST 5 ACTIVE EVENTS"
    )

    compact_active_events = [
        {
            "event_id": event.get(
                "event_id"
            ),
            "event_type": event.get(
                "event_type"
            ),
            "roadway_name": event.get(
                "roadway_name"
            ),
            "timing_status": event.get(
                "timing_status"
            ),
            "closure_scope": event.get(
                "closure_scope"
            ),
            "is_blocking_closure": (
                event.get(
                    "is_blocking_closure"
                )
            ),
            "start_iso_utc": event.get(
                "start_iso_utc"
            ),
            "planned_end_iso_utc": (
                event.get(
                    "planned_end_iso_utc"
                )
            ),
            "description": event.get(
                "description"
            ),
        }
        for event in (
            event_groups["active"][:5]
        )
    ]

    print(
        json.dumps(
            compact_active_events,
            indent=2,
        )
    )

    print_section_title(
        "UPCOMING EVENTS WITHIN 48 HOURS"
    )

    compact_upcoming_events = [
        {
            "event_id": event.get(
                "event_id"
            ),
            "event_type": event.get(
                "event_type"
            ),
            "roadway_name": event.get(
                "roadway_name"
            ),
            "starts_in_hours": event.get(
                "starts_in_hours"
            ),
            "start_iso_utc": event.get(
                "start_iso_utc"
            ),
            "description": event.get(
                "description"
            ),
        }
        for event in (
            event_groups["upcoming"][:10]
        )
    ]

    print(
        json.dumps(
            compact_upcoming_events,
            indent=2,
        )
    )

    print_section_title(
        "END ARIZONA 511 EVENT TIMING TEST"
    )