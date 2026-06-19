"""
route_risk/integrations/state_511_clients/nevada_511_client.py

Nevada 511 traffic-event integration.

Purpose:
- Fetch current Nevada traffic events.
- Normalize Nevada-specific data into the shared road-event structure.
- Separate active, upcoming, future, expired, and unknown-time events.
- Return only active events for route scoring.
- Preserve upcoming events for later informational route summaries.

Run manually from the project root:

    python -m route_risk.integrations.state_511_clients.nevada_511_client
"""

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from route_risk.config import get_nevada_511_api_key


NEVADA_511_EVENTS_URL = (
    "https://www.nvroads.com/api/v2/get/event"
)

DEFAULT_UPCOMING_WINDOW_HOURS = 48.0


def normalize_nevada_event_type(
    event_type: Any,
    event_subtype: Any = None,
    is_full_closure: bool = False,
) -> str:
    """
    Convert Nevada 511 event types into the common event names used by
    the Route Risk Engine.
    """

    normalized_type = str(
        event_type or ""
    ).strip().lower()

    normalized_subtype = str(
        event_subtype or ""
    ).strip().lower()

    if is_full_closure:
        return "road closure"

    if normalized_type == "closures":
        return "road closure"

    if (
        "closure" in normalized_type
        or "closure" in normalized_subtype
    ):
        return "road closure"

    if normalized_type == "roadwork":
        return "construction"

    if (
        "construction" in normalized_type
        or "construction" in normalized_subtype
        or "maintenance" in normalized_type
        or "maintenance" in normalized_subtype
        or "road work" in normalized_subtype
        or "roadwork" in normalized_subtype
        or "lane marking" in normalized_subtype
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


def _parse_unix_timestamp(
    value: Any,
) -> Optional[float]:
    """
    Convert a Nevada Unix timestamp value into a float.

    Returns None when the value is missing, empty, zero, or unusable.
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

    return timestamp


def _timestamp_to_iso_utc(
    timestamp: Optional[float],
) -> Optional[str]:
    """
    Convert a Unix timestamp to an ISO-8601 UTC string.
    """

    if timestamp is None:
        return None

    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).isoformat()


def classify_nevada_event_timing(
    start_date: Any,
    planned_end_date: Any,
    reference_timestamp: Optional[float] = None,
    upcoming_window_hours: float = DEFAULT_UPCOMING_WINDOW_HOURS,
) -> Dict[str, Any]:
    """
    Classify an event according to its start and planned end timestamps.

    Status values:
    - active: started and has not ended
    - upcoming: begins within the configured upcoming window
    - future: begins after the upcoming window
    - expired: planned end date has passed
    - unknown: neither start nor end date is usable
    """

    current_timestamp = (
        float(reference_timestamp)
        if reference_timestamp is not None
        else time.time()
    )

    start_timestamp = _parse_unix_timestamp(
        start_date
    )

    end_timestamp = _parse_unix_timestamp(
        planned_end_date
    )

    upcoming_window_seconds = (
        float(upcoming_window_hours) * 60 * 60
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
            start_timestamp - current_timestamp
        )

        if seconds_until_start <= upcoming_window_seconds:
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


def normalize_nevada_511_event(
    raw_event: Dict[str, Any],
    reference_timestamp: Optional[float] = None,
    upcoming_window_hours: float = DEFAULT_UPCOMING_WINDOW_HOURS,
) -> Dict[str, Any]:
    """
    Normalize one Nevada 511 event into the shared road-event format.

    The primary latitude and longitude are used for checkpoint matching.
    Nevada-specific fields and timing information are preserved.
    """

    event_id = raw_event.get("ID")
    event_type = raw_event.get("EventType")
    event_subtype = raw_event.get("EventSubType")

    is_full_closure = bool(
        raw_event.get(
            "IsFullClosure",
            False,
        )
    )

    normalized_event_type = normalize_nevada_event_type(
        event_type=event_type,
        event_subtype=event_subtype,
        is_full_closure=is_full_closure,
    )

    timing = classify_nevada_event_timing(
        start_date=raw_event.get("StartDate"),
        planned_end_date=raw_event.get(
            "PlannedEndDate"
        ),
        reference_timestamp=reference_timestamp,
        upcoming_window_hours=upcoming_window_hours,
    )

    description = str(
        raw_event.get("Description") or ""
    ).strip()

    comment = str(
        raw_event.get("Comment") or ""
    ).strip()

    if not description and comment:
        description = comment

    if not description:
        description = (
            f"Nevada 511 {normalized_event_type} event"
        )

    return {
        "event_id": f"nevada-511-{event_id}",
        "event_type": normalized_event_type,
        "description": description,
        "latitude": raw_event.get("Latitude"),
        "longitude": raw_event.get("Longitude"),
        "source": "nevada-511-events",

        "timing_status": timing["timing_status"],
        "start_timestamp": timing["start_timestamp"],
        "planned_end_timestamp": timing[
            "planned_end_timestamp"
        ],
        "start_iso_utc": timing["start_iso_utc"],
        "planned_end_iso_utc": timing[
            "planned_end_iso_utc"
        ],
        "starts_in_hours": timing[
            "starts_in_hours"
        ],
        "ends_in_hours": timing[
            "ends_in_hours"
        ],

        # Nevada-specific context retained for later display and filtering.
        "source_event_id": event_id,
        "source_id": raw_event.get("SourceId"),
        "organization": raw_event.get(
            "Organization"
        ),
        "roadway_name": raw_event.get(
            "RoadwayName"
        ),
        "direction_of_travel": raw_event.get(
            "DirectionOfTravel"
        ),
        "event_subtype": event_subtype,
        "is_full_closure": is_full_closure,
        "severity": raw_event.get("Severity"),
        "lanes_affected": raw_event.get(
            "LanesAffected"
        ),
        "reported_unix": raw_event.get(
            "Reported"
        ),
        "last_updated_unix": raw_event.get(
            "LastUpdated"
        ),
        "recurrence": raw_event.get(
            "Recurrence"
        ),
        "recurrence_schedules": raw_event.get(
            "RecurrenceSchedules"
        ),
        "latitude_secondary": raw_event.get(
            "LatitudeSecondary"
        ),
        "longitude_secondary": raw_event.get(
            "LongitudeSecondary"
        ),
        "encoded_polyline": raw_event.get(
            "EncodedPolyline"
        ),
        "detour_polyline": raw_event.get(
            "DetourPolyline"
        ),
        "detour_instructions": raw_event.get(
            "DetourInstructions"
        ),
        "restrictions": raw_event.get(
            "Restrictions"
        ),
        "comment": comment,
    }


def fetch_raw_nevada_511_events(
    timeout_seconds: float = 20.0,
) -> List[Dict[str, Any]]:
    """
    Fetch the current raw traffic-event list from Nevada 511.
    """

    api_key = get_nevada_511_api_key()

    response = requests.get(
        NEVADA_511_EVENTS_URL,
        params={
            "key": api_key,
            "format": "json",
        },
        timeout=timeout_seconds,
    )

    response.raise_for_status()

    response_data = response.json()

    if not isinstance(response_data, list):
        raise RuntimeError(
            "Nevada 511 returned an unexpected response. "
            "Expected a JSON list of traffic events."
        )

    return response_data


def fetch_nevada_511_event_groups(
    timeout_seconds: float = 20.0,
    upcoming_window_hours: float = DEFAULT_UPCOMING_WINDOW_HOURS,
    reference_timestamp: Optional[float] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch, normalize, and group Nevada events by timing status.

    The API is called only once.

    Returned groups:
        active
        upcoming
        future
        expired
        unknown
    """

    raw_events = fetch_raw_nevada_511_events(
        timeout_seconds=timeout_seconds
    )

    groups: Dict[str, List[Dict[str, Any]]] = {
        "active": [],
        "upcoming": [],
        "future": [],
        "expired": [],
        "unknown": [],
    }

    for raw_event in raw_events:
        normalized_event = normalize_nevada_511_event(
            raw_event=raw_event,
            reference_timestamp=reference_timestamp,
            upcoming_window_hours=upcoming_window_hours,
        )

        latitude = normalized_event.get(
            "latitude"
        )

        longitude = normalized_event.get(
            "longitude"
        )

        if latitude is None or longitude is None:
            continue

        timing_status = normalized_event.get(
            "timing_status",
            "unknown",
        )

        if timing_status not in groups:
            timing_status = "unknown"

        groups[timing_status].append(
            normalized_event
        )

    return groups


def fetch_nevada_511_events(
    timeout_seconds: float = 20.0,
) -> List[Dict[str, Any]]:
    """
    Fetch Nevada events that are active at the current time.

    Only these events should affect route scoring.
    Upcoming, future, and expired events are excluded.
    """

    groups = fetch_nevada_511_event_groups(
        timeout_seconds=timeout_seconds
    )

    return groups["active"]


def fetch_nevada_511_upcoming_events(
    timeout_seconds: float = 20.0,
    upcoming_window_hours: float = DEFAULT_UPCOMING_WINDOW_HOURS,
) -> List[Dict[str, Any]]:
    """
    Fetch Nevada events beginning within the upcoming disclosure window.

    These events are informational and should not affect route scoring.
    """

    groups = fetch_nevada_511_event_groups(
        timeout_seconds=timeout_seconds,
        upcoming_window_hours=upcoming_window_hours,
    )

    return groups["upcoming"]


def print_section_title(
    title: str,
) -> None:
    """Print a readable terminal section heading."""

    print(
        "\n============================================================"
    )
    print(title)
    print(
        "============================================================\n"
    )


if __name__ == "__main__":
    print_section_title(
        "NEVADA 511 EVENT TIMING TEST"
    )

    event_groups = fetch_nevada_511_event_groups()

    print(
        f"Active events: {len(event_groups['active'])}"
    )
    print(
        f"Upcoming events within "
        f"{DEFAULT_UPCOMING_WINDOW_HOURS:.0f} hours: "
        f"{len(event_groups['upcoming'])}"
    )
    print(
        f"Future events: {len(event_groups['future'])}"
    )
    print(
        f"Expired events: {len(event_groups['expired'])}"
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
            "start_iso_utc": event.get(
                "start_iso_utc"
            ),
            "planned_end_iso_utc": event.get(
                "planned_end_iso_utc"
            ),
            "description": event.get(
                "description"
            ),
        }
        for event in event_groups["active"][:5]
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
        for event in event_groups["upcoming"][:10]
    ]

    print(
        json.dumps(
            compact_upcoming_events,
            indent=2,
        )
    )

    print_section_title(
        "END NEVADA 511 EVENT TIMING TEST"
    )

