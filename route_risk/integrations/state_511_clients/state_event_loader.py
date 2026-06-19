"""
route_risk/integrations/state_511_clients/state_event_loader.py

Shared loader for state 511 roadway events.
"""

import json
from typing import Any, Callable, Dict, List

from route_risk.integrations.state_511_clients.nevada_511_client import (
    DEFAULT_UPCOMING_WINDOW_HOURS,
    fetch_nevada_511_event_groups,
)


EventList = List[Dict[str, Any]]
EventGroups = Dict[str, EventList]
StateEventGroupFetcher = Callable[..., EventGroups]


STATE_EVENT_GROUP_FETCHERS: Dict[str, StateEventGroupFetcher] = {
    "NV": fetch_nevada_511_event_groups,
}


STATE_NAMES: Dict[str, str] = {
    "ID": "Idaho",
    "NV": "Nevada",
    "UT": "Utah",
    "AZ": "Arizona",
}


EVENT_TIMING_GROUPS = (
    "active",
    "upcoming",
    "future",
    "expired",
    "unknown",
)


def normalize_state_code(state_code: str) -> str:
    normalized_code = str(state_code).strip().upper()

    if len(normalized_code) != 2:
        raise ValueError(
            f"Invalid state code: {state_code!r}. "
            "Expected a two-letter state abbreviation."
        )

    return normalized_code


def normalize_state_codes(
    state_codes: List[str],
) -> List[str]:
    normalized_codes = []

    for state_code in state_codes:
        normalized_code = normalize_state_code(state_code)

        if normalized_code not in normalized_codes:
            normalized_codes.append(normalized_code)

    return normalized_codes


def _empty_event_groups() -> EventGroups:
    return {
        timing_group: []
        for timing_group in EVENT_TIMING_GROUPS
    }


def _add_state_context(
    events: EventList,
    state_code: str,
) -> EventList:
    state_name = STATE_NAMES[state_code]

    return [
        {
            **event,
            "state_code": state_code,
            "state_name": state_name,
        }
        for event in events
    ]


def fetch_event_groups_for_state(
    state_code: str,
    timeout_seconds: float = 20.0,
    upcoming_window_hours: float = DEFAULT_UPCOMING_WINDOW_HOURS,
) -> EventGroups:
    normalized_code = normalize_state_code(state_code)

    if normalized_code not in STATE_NAMES:
        raise ValueError(
            f"Unsupported state code: {normalized_code}. "
            f"Recognized states are: {', '.join(sorted(STATE_NAMES))}."
        )

    group_fetcher = STATE_EVENT_GROUP_FETCHERS.get(normalized_code)

    if group_fetcher is None:
        state_name = STATE_NAMES[normalized_code]

        raise NotImplementedError(
            f"The {state_name} 511 client has not been implemented yet."
        )

    provider_groups = group_fetcher(
        timeout_seconds=timeout_seconds,
        upcoming_window_hours=upcoming_window_hours,
    )

    normalized_groups = _empty_event_groups()

    for timing_group in EVENT_TIMING_GROUPS:
        normalized_groups[timing_group] = _add_state_context(
            events=provider_groups.get(timing_group, []),
            state_code=normalized_code,
        )

    return normalized_groups


def fetch_state_event_groups(
    state_codes: List[str],
    timeout_seconds: float = 20.0,
    upcoming_window_hours: float = DEFAULT_UPCOMING_WINDOW_HOURS,
) -> EventGroups:
    normalized_codes = normalize_state_codes(state_codes)
    combined_groups = _empty_event_groups()

    for state_code in normalized_codes:
        state_groups = fetch_event_groups_for_state(
            state_code=state_code,
            timeout_seconds=timeout_seconds,
            upcoming_window_hours=upcoming_window_hours,
        )

        for timing_group in EVENT_TIMING_GROUPS:
            combined_groups[timing_group].extend(
                state_groups.get(timing_group, [])
            )

    for timing_group in EVENT_TIMING_GROUPS:
        combined_groups[timing_group] = deduplicate_road_events(
            combined_groups[timing_group]
        )

    return combined_groups


def fetch_state_road_events(
    state_codes: List[str],
    timeout_seconds: float = 20.0,
) -> EventList:
    event_groups = fetch_state_event_groups(
        state_codes=state_codes,
        timeout_seconds=timeout_seconds,
    )

    return event_groups["active"]


def fetch_upcoming_state_road_events(
    state_codes: List[str],
    timeout_seconds: float = 20.0,
    upcoming_window_hours: float = DEFAULT_UPCOMING_WINDOW_HOURS,
) -> EventList:
    event_groups = fetch_state_event_groups(
        state_codes=state_codes,
        timeout_seconds=timeout_seconds,
        upcoming_window_hours=upcoming_window_hours,
    )

    return event_groups["upcoming"]


def deduplicate_road_events(
    road_events: EventList,
) -> EventList:
    unique_events = []
    seen_event_keys = set()

    for event in road_events:
        event_key = (
            str(event.get("source", "")),
            str(event.get("event_id", "")),
        )

        if event_key in seen_event_keys:
            continue

        seen_event_keys.add(event_key)
        unique_events.append(event)

    return unique_events


def summarize_events_by_state(
    road_events: EventList,
) -> Dict[str, Dict[str, int]]:
    summary: Dict[str, Dict[str, int]] = {}

    for event in road_events:
        state_code = str(
            event.get("state_code", "UNKNOWN")
        ).upper()

        event_type = str(
            event.get("event_type", "unknown")
        ).lower()

        state_summary = summary.setdefault(
            state_code,
            {
                "total": 0,
                "construction": 0,
                "road closure": 0,
                "incident": 0,
                "caution": 0,
            },
        )

        state_summary["total"] += 1

        if event_type not in state_summary:
            state_summary[event_type] = 0

        state_summary[event_type] += 1

    return summary


def print_section_title(title: str) -> None:
    print("\n============================================================")
    print(title)
    print("============================================================\n")


if __name__ == "__main__":
    print_section_title("STATE 511 EVENT GROUP LOADER TEST")

    requested_states = ["NV"]

    event_groups = fetch_state_event_groups(
        state_codes=requested_states,
    )

    active_events = event_groups["active"]
    upcoming_events = event_groups["upcoming"]

    print(f"Requested states: {requested_states}")
    print(f"Active scoring events: {len(active_events)}")
    print(f"Upcoming disclosure events: {len(upcoming_events)}")
    print(f"Future events: {len(event_groups['future'])}")
    print(f"Expired events: {len(event_groups['expired'])}")
    print(f"Unknown-time events: {len(event_groups['unknown'])}")

    print_section_title("ACTIVE EVENT SUMMARY")

    print(
        json.dumps(
            summarize_events_by_state(active_events),
            indent=2,
        )
    )

    print_section_title("UPCOMING EVENT SUMMARY")

    print(
        json.dumps(
            summarize_events_by_state(upcoming_events),
            indent=2,
        )
    )

    print_section_title("FIRST 5 UPCOMING EVENTS")

    compact_upcoming_events = [
        {
            "event_id": event.get("event_id"),
            "state_code": event.get("state_code"),
            "event_type": event.get("event_type"),
            "roadway_name": event.get("roadway_name"),
            "starts_in_hours": event.get("starts_in_hours"),
            "start_iso_utc": event.get("start_iso_utc"),
            "description": event.get("description"),
        }
        for event in upcoming_events[:5]
    ]

    print(
        json.dumps(
            compact_upcoming_events,
            indent=2,
        )
    )

    print_section_title("END STATE 511 EVENT GROUP LOADER TEST")