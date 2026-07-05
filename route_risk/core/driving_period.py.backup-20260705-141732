"""Shared day/night applicability rules for normalized road events."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, dict):
        return " ".join(
            _flatten_text(item)
            for item in value.values()
        )

    if isinstance(value, (list, tuple, set)):
        return " ".join(
            _flatten_text(item)
            for item in value
        )

    return str(value)


def _event_search_text(
    event: Dict[str, Any],
) -> str:
    fields = (
        "description",
        "comment",
        "restrictions",
        "recurrence",
        "recurrence_schedules",
        "schedule",
        "event_type",
        "event_subtype",
        "lanes_affected",
    )

    return re.sub(
        r"\s+",
        " ",
        " ".join(
            _flatten_text(
                event.get(field_name)
            )
            for field_name in fields
        ),
    ).strip().lower()


def _parse_clock_hour(
    text: str,
) -> Optional[float]:
    match = re.fullmatch(
        r"\s*(\d{1,2})"
        r"(?::(\d{2}))?"
        r"\s*([ap])\.?m\.?\s*",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    hour = int(match.group(1))
    minute = int(
        match.group(2)
        or 0
    )
    meridiem = match.group(3).lower()

    if not 1 <= hour <= 12:
        return None

    if not 0 <= minute <= 59:
        return None

    if hour == 12:
        hour = 0

    if meridiem == "p":
        hour += 12

    return hour + (
        minute / 60.0
    )


def _extract_time_range(
    text: str,
) -> Optional[Tuple[float, float]]:
    time_token = (
        r"\d{1,2}"
        r"(?::\d{2})?"
        r"\s*[ap]\.?m\.?"
    )

    match = re.search(
        rf"({time_token})\s*"
        rf"(?:-|–|—|to|through|until|and)\s*"
        rf"({time_token})",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    start_hour = _parse_clock_hour(
        match.group(1)
    )

    end_hour = _parse_clock_hour(
        match.group(2)
    )

    if (
        start_hour is None
        or end_hour is None
    ):
        return None

    return (
        start_hour,
        end_hour,
    )


def _period_from_time_range(
    start_hour: float,
    end_hour: float,
) -> Optional[str]:
    duration = (
        end_hour - start_hour
    ) % 24.0

    if duration == 0 or duration >= 20:
        return "all_day"

    crosses_midnight = (
        end_hour < start_hour
    )

    if crosses_midnight:
        if (
            start_hour >= 17
            and end_hour <= 9
        ):
            return "night_only"

        return None

    if (
        start_hour >= 17
        and end_hour <= 24
    ):
        return "night_only"

    if (
        start_hour < 7
        and end_hour <= 9
    ):
        return "night_only"

    if (
        start_hour >= 5
        and end_hour <= 20
    ):
        return "day_only"

    return None


def classify_event_driving_period(
    event: Dict[str, Any],
) -> Dict[str, str]:
    """
    Classify an event using human-readable event details before provider
    recurrence metadata.

    Some state feeds mark a multi-day project as active all day while the
    description clearly states that the actual closure occurs only nightly.
    Explicit description/comment timing therefore takes precedence.
    """

    explicit_period = str(
        event.get(
            "driving_period",
            "",
        )
    ).strip().lower()

    explicit_aliases = {
        "all": "all_day",
        "all_day": "all_day",
        "all-day": "all_day",
        "24_hour": "all_day",
        "24_hours": "all_day",
        "day": "day_only",
        "day_only": "day_only",
        "daytime": "day_only",
        "night": "night_only",
        "night_only": "night_only",
        "nighttime": "night_only",
        "unknown": "unknown",
    }

    if explicit_period in explicit_aliases:
        return {
            "driving_period": (
                explicit_aliases[
                    explicit_period
                ]
            ),
            "driving_period_source": (
                "provider_normalized"
            ),
        }

    narrative_fields = (
        "description",
        "comment",
        "restrictions",
        "lanes_affected",
        "event_type",
        "event_subtype",
    )

    narrative_text = re.sub(
        r"\s+",
        " ",
        " ".join(
            _flatten_text(
                event.get(field_name)
            )
            for field_name in narrative_fields
        ),
    ).strip().lower()

    # Explicit night wording in the event description wins over conflicting
    # structured schedules from a provider.
    if re.search(
        r"\b(?:nightly|overnight|"
        r"each\s+night|every\s+night|"
        r"at\s+night|nighttime|"
        r"night\s+work|night\s+closure|"
        r"sunset\s+to\s+sunrise)\b",
        narrative_text,
        flags=re.IGNORECASE,
    ):
        return {
            "driving_period": "night_only",
            "driving_period_source": (
                "narrative_night_keyword"
            ),
        }

    # Explicit daytime wording also overrides generic recurrence metadata.
    if re.search(
        r"\b(?:daytime|during\s+the\s+day|"
        r"daylight\s+hours?|day\s+work|"
        r"day\s+closure|sunrise\s+to\s+sunset)\b",
        narrative_text,
        flags=re.IGNORECASE,
    ):
        return {
            "driving_period": "day_only",
            "driving_period_source": (
                "narrative_day_keyword"
            ),
        }

    narrative_time_range = (
        _extract_time_range(
            narrative_text
        )
    )

    if narrative_time_range:
        inferred_period = (
            _period_from_time_range(
                start_hour=(
                    narrative_time_range[0]
                ),
                end_hour=(
                    narrative_time_range[1]
                ),
            )
        )

        if inferred_period:
            return {
                "driving_period": (
                    inferred_period
                ),
                "driving_period_source": (
                    "narrative_time_range"
                ),
            }

    # Only consult the complete provider payload after narrative timing has
    # failed to identify a specific day/night period.
    search_text = _event_search_text(
        event
    )

    if re.search(
        r"\b(?:nightly|overnight|"
        r"each\s+night|every\s+night|"
        r"at\s+night|nighttime|"
        r"night\s+work|night\s+closure|"
        r"sunset\s+to\s+sunrise)\b",
        search_text,
        flags=re.IGNORECASE,
    ):
        return {
            "driving_period": "night_only",
            "driving_period_source": (
                "provider_night_keyword"
            ),
        }

    if re.search(
        r"\b(?:daytime|during\s+the\s+day|"
        r"daylight\s+hours?|day\s+work|"
        r"day\s+closure|sunrise\s+to\s+sunset)\b",
        search_text,
        flags=re.IGNORECASE,
    ):
        return {
            "driving_period": "day_only",
            "driving_period_source": (
                "provider_day_keyword"
            ),
        }

    provider_time_range = (
        _extract_time_range(
            search_text
        )
    )

    if provider_time_range:
        inferred_period = (
            _period_from_time_range(
                start_hour=(
                    provider_time_range[0]
                ),
                end_hour=(
                    provider_time_range[1]
                ),
            )
        )

        if inferred_period:
            return {
                "driving_period": (
                    inferred_period
                ),
                "driving_period_source": (
                    "provider_time_range"
                ),
            }

    if re.search(
        r"\b(?:24\s*/\s*7|24\s*hours?|"
        r"all[\s-]?day|continuous(?:ly)?)\b",
        search_text,
        flags=re.IGNORECASE,
    ):
        return {
            "driving_period": "all_day",
            "driving_period_source": (
                "all_day_keyword"
            ),
        }

    return {
        "driving_period": "unknown",
        "driving_period_source": (
            "no_specific_schedule"
        ),
    }



def event_applies_to_driving_period(
    event_period: str,
    is_night: bool,
) -> bool:
    normalized_period = str(
        event_period or "unknown"
    ).strip().lower()

    if normalized_period in {
        "all_day",
        "unknown",
    }:
        return True

    if normalized_period == "night_only":
        return bool(is_night)

    if normalized_period == "day_only":
        return not bool(is_night)

    return True


def apply_driving_period_to_events(
    events: Iterable[Dict[str, Any]],
    is_night: bool,
) -> List[Dict[str, Any]]:
    selected_period = (
        "night"
        if is_night
        else "day"
    )

    applicable_events: List[
        Dict[str, Any]
    ] = []

    for original_event in events:
        event = dict(original_event)

        classification = (
            classify_event_driving_period(
                event
            )
        )

        event_period = classification[
            "driving_period"
        ]

        applies = (
            event_applies_to_driving_period(
                event_period=event_period,
                is_night=is_night,
            )
        )

        event.update(
            classification
        )

        event[
            "selected_driving_period"
        ] = selected_period

        event[
            "driving_period_applies"
        ] = applies

        if applies:
            applicable_events.append(
                event
            )

    return applicable_events
